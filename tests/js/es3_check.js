// Verifica que um .jsx é ES3 válido (ExtendScript não aceita sintaxe ES5+).
const fs = require("fs");
const path = require("path");
let acorn;
for (const p of [process.env.ACORN_PATH, "acorn"]) {
  try { if (p) { acorn = require(p); break; } } catch (e) { /* tenta o próximo */ }
}
if (!acorn) { console.error("acorn não encontrado (npm install acorn ou ACORN_PATH)"); process.exit(2); }
let bad = 0;
for (const f of process.argv.slice(2)) {
  const src = fs.readFileSync(f, "utf8");
  try {
    acorn.parse(src, { ecmaVersion: 3, allowReserved: false, sourceType: "script" });
    // APIs ES5 que o ExtendScript não tem
    const es5 = src.match(/\.(forEach|indexOf|map|filter|reduce|trim|bind)\s*\(|JSON\.(parse|stringify)|Object\.keys/g);
    if (es5) { console.error(`${path.basename(f)}: API ES5 indisponível no ExtendScript: ${[...new Set(es5)].join(", ")}`); bad++; }
    else console.log(`${path.basename(f)}: ES3 OK (${src.length} bytes)`);
  } catch (e) {
    const line = src.split("\n")[e.loc.line - 1] || "";
    console.error(`${path.basename(f)}:${e.loc.line}:${e.loc.column} ${e.message}\n  ${line.slice(Math.max(0, e.loc.column - 60), e.loc.column + 60)}`);
    bad++;
  }
}
process.exit(bad ? 1 : 0);
