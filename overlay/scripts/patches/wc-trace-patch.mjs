// wc-trace-patch.mjs — instrumenta o CONSTRUTOR da classe do erro para gravar stack.
// Equivale ao claim-trace, mas via patch no dist (sem depender de NODE_OPTIONS).
import { readFileSync, writeFileSync, readdirSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

const MARK = "__lzbot_wctrace__";
const LOG = "/app/workspace/debug/wc-stack.log";
const dist = process.argv[2];
const say = (m) => console.log(`  writer-claim-trace: ${m}`);

const cands = readdirSync(dist).filter((f) => /^transcript-write-context-.*\.mjs$/.test(f));
if (cands.length !== 1) { say(`AVISO ${cands.length} bundles transcript-write-context`); process.exit(3); }
const file = join(dist, cands[0]);
let src = readFileSync(file, "utf8");
if (src.includes(MARK)) { say("ja aplicado"); process.exit(0); }

const A =
  'var SessionTranscriptWriterClaimReboundError = class extends Error {\n' +
  '\tconstructor(cause) {\n' +
  '\t\tsuper("session writer claim changed before transcript persistence", { cause });\n' +
  '\t\tthis.name = "SessionTranscriptWriterClaimReboundError";\n' +
  '\t}\n' +
  '};';
if (src.split(A).length - 1 !== 1) { say("AVISO ancora do construtor nao bateu"); process.exit(3); }

const NEW =
  'var SessionTranscriptWriterClaimReboundError = class extends Error {\n' +
  '\tconstructor(cause) {\n' +
  '\t\tsuper("session writer claim changed before transcript persistence", { cause });\n' +
  '\t\tthis.name = "SessionTranscriptWriterClaimReboundError";\n' +
  `\t\t/* ${MARK} */ try { require("node:fs").appendFileSync(${JSON.stringify(LOG)}, "=== " + new Date().toISOString() + " pid=" + process.pid + "\\ncause: " + String(cause && (cause.stack || cause)) + "\\n" + this.stack + "\\n\\n"); } catch {}\n` +
  '\t}\n' +
  '};';

// bundle é ESM: sem require. injeta createRequire uma vez, no topo.
const SHIM = `import { createRequire as __wcCreateRequire } from "node:module";\nconst require = __wcCreateRequire(import.meta.url);\nError.stackTraceLimit = 60;\n`;
src = SHIM + src.replace(A, NEW);

writeFileSync(file + ".orig", readFileSync(file));
writeFileSync(file, src);
try { execFileSync(process.execPath, ["--check", file], { stdio: "ignore" }); }
catch { writeFileSync(file, readFileSync(file + ".orig")); say("AVISO node --check falhou, restaurado"); process.exit(3); }
say(`aplicado em ${cands[0]} (log: ${LOG})`);
