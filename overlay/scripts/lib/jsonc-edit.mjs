// jsonc-edit.mjs — edicao de JSON5/JSONC PRESERVANDO comentario e formatacao.
//
// ESCOPO: este helper existe para UM arquivo — `/app/agents.json5`.
//
// O `openclaw.json` NAO usa isso e nao deve: ele e JSON estrito de proposito
// (ver openclaw.json.md). O produto trata aquele arquivo como JSON puro —
// `openclaw config set` reescreve com JSON.parse/JSON.stringify e apaga todo
// comentario (medido no binario 2026.9.1: 83 -> 0) — entao manter comentario la
// e remar contra o produto. Para o openclaw.json, JSON.parse/stringify e a
// escolha certa.
//
// O `agents.json5` e o caso oposto, e por isso ganha tratamento especial: o
// produto NAO reescreve esse arquivo (nem `config set`, nem o loader), entao os
// comentarios dele sobrevivem indefinidamente — e valem, porque documentam
// decisao de seguranca por agente (allowlist de tools do concierge, isolamento
// de workspace, etc.). A migration 0005 precisa converte-lo de array para
// objeto chaveado, e essa conversao nao pode custar a documentacao.
//
// Ferramenta: `jsonc-parser` (o mesmo que o OpenClaw usa inlined no seu
// `oc-path`). Nao e dependencia exportada do openclaw@2026.9.1, entao a imagem
// instala explicitamente — ver Dockerfile.

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";

function loadJsonc() {
  const require_ = createRequire(import.meta.url);
  const candidates = [];
  if (process.env.LZBOT_JSONC_PARSER_PATH) candidates.push(process.env.LZBOT_JSONC_PARSER_PATH);
  candidates.push("jsonc-parser");
  try {
    const globalRoot = execFileSync("npm", ["root", "-g"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    if (globalRoot) candidates.push(`${globalRoot}/jsonc-parser`);
  } catch {}
  for (const c of candidates) {
    try {
      return require_(c);
    } catch {}
  }
  throw new Error(
    "jsonc-parser nao encontrado. A imagem instala via `npm install -g jsonc-parser@3.3.1` (ver Dockerfile). " +
      "Em dev: instale global ou aponte LZBOT_JSONC_PARSER_PATH=<caminho do modulo>.",
  );
}

const jsonc = loadJsonc();
const FORMAT = { formattingOptions: { insertSpaces: false, tabSize: 1, keepLines: true } };

/** Le e faz parse tolerante a comentario/virgula sobrando. */
export function readJsonc(path) {
  const text = fs.readFileSync(path, "utf8");
  const errors = [];
  const data = jsonc.parse(text, errors, { allowTrailingComma: true, disallowComments: false });
  if (errors.length > 0) {
    const e = errors[0];
    throw new Error(`${path}: JSON5 invalido (codigo ${e.error}) no offset ${e.offset}`);
  }
  return { text, data: data ?? {} };
}

export function getIn(obj, path) {
  let cur = obj;
  for (const k of path) {
    if (cur === null || typeof cur !== "object" || !(k in cur)) return undefined;
    cur = cur[k];
  }
  return cur;
}

export function hasIn(obj, path) {
  let cur = obj;
  for (const k of path) {
    if (cur === null || typeof cur !== "object" || !(k in cur)) return false;
    cur = cur[k];
  }
  return true;
}

// Ops:
//   { op: "set",    path, value }            sobrescreve
//   { op: "fill",   path, value }            so se AUSENTE
//   { op: "remove", path }                   no-op se ausente
//   { op: "move",   from, to }               no-op se `from` ausente
//
// Re-parseia a cada passo porque um edit desloca os offsets dos seguintes.
export function editJsonc(text, ops) {
  let out = text;
  let changed = false;
  const parse = () => jsonc.parse(out, [], { allowTrailingComma: true }) ?? {};
  const write = (path, value) => {
    const edits = jsonc.modify(out, path, value, FORMAT);
    if (edits.length === 0) return false;
    out = jsonc.applyEdits(out, edits);
    return true;
  };

  for (const op of ops) {
    switch (op.op) {
      case "set":
        if (write(op.path, op.value)) changed = true;
        break;
      case "fill":
        if (!hasIn(parse(), op.path) && write(op.path, op.value)) changed = true;
        break;
      case "remove":
        // `undefined` em modify() = remocao da chave (contrato do jsonc-parser).
        if (hasIn(parse(), op.path) && write(op.path, undefined)) changed = true;
        break;
      case "move": {
        const data = parse();
        if (!hasIn(data, op.from)) break;
        const value = getIn(data, op.from);
        if (!hasIn(data, op.to)) write(op.to, value);
        write(op.from, undefined);
        changed = true;
        break;
      }
      default:
        throw new Error(`op desconhecida: ${op.op}`);
    }
  }
  return { text: out, changed };
}

//// Array -> objeto chaveado, preservando comentario
// `jsonc.modify` na raiz reserializaria o arquivo inteiro (perdendo tudo), entao
// aqui e cirurgia por OFFSET sobre a AST: trocamos so os bytes de `[`/`]`,
// injetamos `"<chave>": ` antes de cada elemento e removemos a propriedade que
// virou chave. Os bytes ENTRE elementos — que e onde moram os comentarios —
// nunca sao tocados. Edits aplicados de tras pra frente para nao invalidar
// offsets.
export function arrayToRecord(text, keyProp) {
  const root = jsonc.parseTree(text, [], { allowTrailingComma: true });
  if (!root || root.type !== "array") return { text, changed: false, keys: [] };

  const edits = [];
  const keys = [];

  for (const element of root.children ?? []) {
    if (element.type !== "object")
      throw new Error(`elemento do array nao e objeto (offset ${element.offset})`);
    const propNode = (element.children ?? []).find(
      (p) => p.type === "property" && p.children?.[0]?.value === keyProp,
    );
    if (!propNode) throw new Error(`elemento sem "${keyProp}" (offset ${element.offset})`);
    const key = propNode.children[1].value;
    keys.push(key);

    // Remove a propriedade-chave + virgula + a quebra de linha, e tambem a
    // indentacao da linha, senao sobra uma linha em branco no lugar.
    let end = propNode.offset + propNode.length;
    while (end < text.length && /[ \t]/.test(text[end])) end++;
    if (text[end] === ",") end++;
    while (end < text.length && /[ \t]/.test(text[end])) end++;
    if (text[end] === "\n") end++;
    let start = propNode.offset;
    while (start > 0 && /[ \t]/.test(text[start - 1])) start--;
    edits.push({ offset: start, length: end - start, content: "" });

    // Injeta a chave antes do `{` do elemento.
    edits.push({ offset: element.offset, length: 0, content: `${JSON.stringify(key)}: ` });
  }

  edits.push({ offset: root.offset, length: 1, content: "{" });
  edits.push({ offset: root.offset + root.length - 1, length: 1, content: "}" });

  edits.sort((a, b) => b.offset - a.offset || b.length - a.length);
  let out = text;
  for (const e of edits) out = out.slice(0, e.offset) + e.content + out.slice(e.offset + e.length);

  return { text: out, changed: true, keys };
}

//// Renomeia uma propriedade E aninha o valor dela, preservando comentario
// Motivacao: `editJsonc({op:"move"})` NAO serve para isto. `jsonc.modify`, ao
// remover uma propriedade, leva junto o bloco de comentario que estava
// imediatamente ACIMA dela (trata o comentario como parte do whitespace da
// propriedade). Medido: `memorySearch` -> `memory.search` via move custou 8 dos
// 38 comentarios do agents.json5 — justamente os que explicavam a decisao.
//
// Aqui a edicao e por OFFSET: troca so o token da CHAVE e envelopa o texto do
// VALOR. Nada antes da propriedade e tocado, entao o comentario fica onde estava.
export function renameAndNest(text, objectKey, fromProp, toProp, innerProp) {
  const root = jsonc.parseTree(text, [], { allowTrailingComma: true });
  if (!root) return { text, changed: false };

  const container =
    objectKey === null
      ? root
      : (root.children ?? []).find(
          (p) => p.type === "property" && p.children?.[0]?.value === objectKey,
        )?.children?.[1];
  if (!container || container.type !== "object") return { text, changed: false };

  const prop = (container.children ?? []).find(
    (p) => p.type === "property" && p.children?.[0]?.value === fromProp,
  );
  if (!prop) return { text, changed: false };

  const keyNode = prop.children[0];
  const valueNode = prop.children[1];

  // Indentacao da linha da propriedade, para reindentar o valor um nivel abaixo.
  let lineStart = text.lastIndexOf("\n", prop.offset) + 1;
  const indent = text.slice(lineStart, prop.offset);
  const unit = indent.includes("\t") ? "\t" : "  ";

  const valueText = text.slice(valueNode.offset, valueNode.offset + valueNode.length);
  const nested = valueText
    .split("\n")
    .map((l, i) => (i === 0 ? l : unit + l))
    .join("\n");
  const wrapped = `{\n${indent}${unit}${JSON.stringify(innerProp)}: ${nested}\n${indent}}`;

  // De tras pra frente: valor primeiro, depois a chave.
  let out =
    text.slice(0, valueNode.offset) + wrapped + text.slice(valueNode.offset + valueNode.length);
  out =
    out.slice(0, keyNode.offset) +
    JSON.stringify(toProp) +
    out.slice(keyNode.offset + keyNode.length);

  return { text: out, changed: true };
}
