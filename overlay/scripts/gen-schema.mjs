#!/usr/bin/env node
// gen-schema.mjs — lê o schema do openclaw.json no stdin, conserta os $ref dos
// sub-schemas de plugin e grava o resultado.
//
// Usage: openclaw config schema --json | node scripts/gen-schema.mjs doc/schema.json
//
// O openclaw embute o schema de cada plugin no schema grande sem reescrever os
// $ref internos: eles continuam apontando para a raiz ("#/$defs/route"), mas o
// $defs que os define fica aninhado sob o nó do plugin. Como a raiz não tem
// $defs, todo ref desses fica quebrado e o editor acusa erro no openclaw.json.

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

const OUT = process.argv[2];

if (!OUT) {
  console.error("Usage: node scripts/gen-schema.mjs <arquivo-de-saida>");
  process.exit(2);
}

/// Escapar um segmento de JSON Pointer (RFC 6901)
function escapeToken(token) {
  return token.replace(/~/g, "~0").replace(/\//g, "~1");
}

/// Reescrever "#/$defs/NAME" para o $defs mais próximo que realmente define NAME
function fixRefs(root) {
  let fixed = 0;
  const scopes = [];

  const walk = (node, pointer) => {
    if (node === null || typeof node !== "object") return;

    if (Array.isArray(node)) {
      node.forEach((item, i) => walk(item, `${pointer}/${i}`));
      return;
    }

    const defs = node.$defs;
    const hasScope = defs !== null && typeof defs === "object";

    if (hasScope) scopes.push({ pointer, names: new Set(Object.keys(defs)) });

    if (typeof node.$ref === "string") {
      const match = /^#\/\$defs\/([^/]+)(\/.*)?$/.exec(node.$ref);

      if (match) {
        const [, name, rest = ""] = match;
        // Do escopo mais interno para o mais externo: o primeiro que
        // define o nome é o dono legítimo do ref
        const owner = [...scopes].reverse().find((scope) => scope.names.has(name));

        if (owner && owner.pointer !== "") {
          node.$ref = `#${owner.pointer}/$defs/${escapeToken(name)}${rest}`;
          fixed++;
        }
      }
    }

    for (const key of Object.keys(node)) {
      walk(node[key], `${pointer}/${escapeToken(key)}`);
    }

    if (hasScope) scopes.pop();
  };

  walk(root, "");

  return fixed;
}

/// Conferir que todo $ref interno resolve, para não gravar um schema quebrado
function findBrokenRefs(root) {
  const broken = [];

  const resolve = (ref) => {
    if (!ref.startsWith("#")) return true; // ref externo: fora do nosso escopo

    const tokens = ref
      .slice(1)
      .split("/")
      .filter(Boolean)
      .map((token) => token.replace(/~1/g, "/").replace(/~0/g, "~"));

    let current = root;

    for (const token of tokens) {
      if (current === null || typeof current !== "object" || !(token in current)) return false;
      current = current[token];
    }

    return true;
  };

  const walk = (node, pointer) => {
    if (node === null || typeof node !== "object") return;

    if (typeof node.$ref === "string" && !resolve(node.$ref)) {
      broken.push({ ref: node.$ref, at: pointer || "/" });
    }

    for (const key of Object.keys(node)) {
      walk(node[key], `${pointer}/${key}`);
    }
  };

  walk(root, "");

  return broken;
}

let input = "";

for await (const chunk of process.stdin) input += chunk;

// Tolerar qualquer ruído antes do JSON (banner, aviso de npm etc.)
const start = input.indexOf("{");

if (start === -1) {
  console.error("gen-schema: nenhum JSON na entrada (o openclaw imprimiu algo?)");
  process.exit(1);
}

let schema;

try {
  schema = JSON.parse(input.slice(start));
} catch (error) {
  console.error(`gen-schema: entrada não é JSON válido — ${error.message}`);
  process.exit(1);
}

const fixed = fixRefs(schema);
const broken = findBrokenRefs(schema);

if (broken.length > 0) {
  console.error(`gen-schema: ${broken.length} $ref continuam sem resolver, nada foi gravado:`);
  for (const { ref, at } of broken.slice(0, 10)) console.error(`  ${ref}  (em ${at})`);
  process.exit(1);
}

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, `${JSON.stringify(schema, null, 2)}\n`);

console.log(`gen-schema: ${OUT} gravado (${fixed} \$ref corrigidos)`);
