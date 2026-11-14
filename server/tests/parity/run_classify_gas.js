// apps-script 原版 classifyChunks_：genJson_ 依序回傳測試給的回覆，記下 prompt
const fs = require("fs"), vm = require("vm"), path = require("path");
const gas = path.join(__dirname, "..", "..", "..", "apps-script");
const read = (f) => fs.readFileSync(path.join(gas, f), "utf8");
const src = read("Setup.gs").replace("function setup()", "function setupX__()") + "\n" + read("Knowledge.gs");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const sb = { console: { warn() {} } }; vm.createContext(sb);
vm.runInContext(src + `
  let __c = null, __p = [];
  function cfgAll_() { return __c.cfg; } function cfg_(k) { return __c.cfg[k]; }
  function genJson_(m, p) { __p.push(p); const r = __c.resp[__p.length - 1]; if (r && r.__throw) throw new Error(r.__throw); return r; }
  this.__run = (c) => { __c = c; __p = []; const out = classifyChunks_(c.texts); return { out: out, prompts: __p }; };`, sb);
process.stdout.write(JSON.stringify(cases.map((c) => sb.__run(c))));
