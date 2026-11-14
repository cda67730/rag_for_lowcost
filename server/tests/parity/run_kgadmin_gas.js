// apps-script 原版 Graph.gs 的標註、接法提醒、產生圖譜、Ontology；試算表與模型由測試給定
const fs = require("fs"), vm = require("vm"), path = require("path");
const gas = path.join(__dirname, "..", "..", "..", "apps-script");
const read = (f) => fs.readFileSync(path.join(gas, f), "utf8");
const src = ["Setup.gs", "Chat.gs", "Knowledge.gs", "Graph.gs"].map(read).join("\n").replace("function setup()", "function setupX__()");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const sb = { console: { warn() {}, log() {} } }; vm.createContext(sb);
vm.runInContext(src + `
  const __base = {}; SETTINGS_DEFAULT.forEach(r => __base[r[0]] = r[1]);
  let __c = null, __i = 0, __p = [];
  function cfgAll_() { return __c.cfg; } function cfg_(k) { return __c.cfg[k]; } function requireAdmin_() { return 'me'; } function stabNote_() {}
  function rows_(t) { return JSON.parse(JSON.stringify(__c.sheets[t] || [])); }
  function genJson_(m, p) { __p.push(p); const r = __c.resp[__i++]; if (r && r.__throw) throw new Error(r.__throw); return JSON.parse(JSON.stringify(r)); }
  kgGenChunks_ = function () { return __c.genChunks; };
  Utilities = { formatDate: (d, tz, f) => ({ 'yyMMddHHmm': '2611141200', 'yyyy.MM.dd-HHmm': '2026.11.14-1200', 'yyyy-MM-dd': '2026-11-14' }[f]) };
  this.__run = (c) => { __c = c; __c.cfg = Object.assign({}, __base, c.cfg || {}); __i = 0; __p = [];
    let out;
    if (c.fn === 'map') out = kgMapChunks_(c.title, c.chunks);
    if (c.fn === 'warn') out = c.args.map(a => kgRelWarn_(kgPairs_(rows_('kgNodes'), rows_('kgEdges')), kgDefs_(), a[0], a[1], a[2]));
    if (c.fn === 'gen') out = adminKgGenBatch(c.i);
    if (c.fn === 'finish') { const r = adminKgGenFinish(c.items); out = { json: JSON.parse(r.json), ttl: r.ttl, kept: r.kept, merged: r.merged }; }
    return { out: out, prompts: __p }; };`, sb);
process.stdout.write(JSON.stringify(cases.map((c) => sb.__run(c))));
