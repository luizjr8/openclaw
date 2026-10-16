// wc-cli-guard.mjs — fix do writer-claim (upstream openclaw#154572).
// Stack confirmado por trace: prepareCliHistoryBoundary -> SessionManager.openBoundedAsync
// -> assertTranscriptWriteContext -> throw. O contexto ALS pertence a sessao PAI e o scope
// lido e o da FILHA; sessionId difere => throw. Sessao nova nao tem historico de CLI a
// reusar, entao pular a LEITURA quando o contexto nao e do alvo e semanticamente neutro.
import { readFileSync, writeFileSync, readdirSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

const MARK = "__lzbot_wccli__";
const LOG = "/app/workspace/debug/wc-guard.log";
const dist = process.argv[2];
const say = (m) => console.log(`  writer-claim-guard: ${m}`);

const cands = readdirSync(dist).filter((f) => /^prepare\.runtime-.*\.mjs$/.test(f))
  .filter((f) => readFileSync(join(dist, f), "utf8").includes("prepareCliHistoryBoundary") || readFileSync(join(dist, f), "utf8").includes("cliHistoryBoundary"));
if (cands.length !== 1) { say(`AVISO ${cands.length} candidatos prepare.runtime`); process.exit(3); }
const file = join(dist, cands[0]);
let src = readFileSync(file, "utf8");
if (src.includes(MARK)) { say("ja aplicado"); process.exit(0); }

const A = "\tif (!allowed && fingerprint && currentUserIsLast && !params.cliSessionId && !params.cliSessionBinding) {";
if (src.split(A).length - 1 !== 1) { say("AVISO ancora nao bateu (1x esperado)"); process.exit(3); }
if (!src.includes("assertOwnedTranscriptWriteCommit")) { say("AVISO assertOwnedTranscriptWriteCommit ausente"); process.exit(3); }

const NEW = `\t/* ${MARK} */ let __wcCtxOk = true;
\ttry { assertOwnedTranscriptWriteCommit(target); } catch (__e) {
\t\tif (__e && __e.name === "SessionTranscriptWriterClaimReboundError") {
\t\t\t__wcCtxOk = false;
\t\t\ttry { (await import("node:fs")).appendFileSync(${JSON.stringify(LOG)}, JSON.stringify({ t: new Date().toISOString(), pid: process.pid, skip: "cli-history-read", sessionId: target?.sessionId ?? null, agentId: target?.agentId ?? null }) + "\\n"); } catch {}
\t\t} else throw __e;
\t}
${A.replace("if (!allowed", "if (__wcCtxOk && !allowed")}`;

src = src.replace(A, NEW);
writeFileSync(file + ".orig", readFileSync(file));
writeFileSync(file, src);
try { execFileSync(process.execPath, ["--check", file], { stdio: "ignore" }); }
catch { writeFileSync(file, readFileSync(file + ".orig")); say("AVISO node --check falhou, restaurado"); process.exit(3); }
say(`aplicado em ${cands[0]} (log: ${LOG})`);
