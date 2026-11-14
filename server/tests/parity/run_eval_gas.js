// apps-script 原版 Eval.gs：出題、評分、摘要；問答結果與模型回覆由測試給定
const fs = require("fs"), vm = require("vm"), path = require("path");
const gas = path.join(__dirname, "..", "..", "..", "apps-script");
const read = (f) => fs.readFileSync(path.join(gas, f), "utf8");
const src = ["Setup.gs", "Chat.gs", "Knowledge.gs", "Graph.gs", "Eval.gs"].map(read).join("\n").replace("function setup()", "function setupX__()");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const sb = { console: { warn() {}, log() {} } }; vm.createContext(sb);
vm.runInContext(src + `
  const __base = {}; SETTINGS_DEFAULT.forEach(r => __base[r[0]] = r[1]);
  let __c = null, __i = 0, __p = [], __added = null, __row = null;
  function cfgAll_() { return __c.cfg; } function cfg_(k) { return __c.cfg[k]; } function requireAdmin_() { return 'me'; } function stabNote_() {}
  function truthy_(v) { return v === true || String(v).toUpperCase() === 'TRUE' || v === '是'; }
  function rows_(t) { return JSON.parse(JSON.stringify((__c.sheets || {})[t] || [])); }
  function referral_() { return __c.refs || {}; } function forbidden_() { return __c.forbidden || []; }
  function genJson_(m, p, o) { __p.push([m, p, !!(o && o.noThink)]); const r = __c.resp[__i++]; if (r && r.__throw) throw new Error(r.__throw); return JSON.parse(JSON.stringify(r)); }
  kgGenChunks_ = function () { return __c.genChunks; };
  evalAddQs_ = function (list) { __added = list; return list.length; };
  runChat_ = function () { if (__c.res.__throw) throw new Error(__c.res.__throw); return JSON.parse(JSON.stringify(__c.res)); };
  kgHead_ = function () { return Object.keys(__c.head); };
  append_ = function (t, rows) { __row = rows[0]; };
  Math.random = () => __c.rnd;
  this.__run = (c) => { __c = c; __c.cfg = Object.assign({}, __base, c.cfg || {}); __i = 0; __p = []; __added = null; __row = null; SCHEMA_ = null;
    let out;
    if (c.fn === 'gen') { adminEvalGenBatch(c.ids); out = __added; }
    if (c.fn === 'special') { adminEvalGenSpecial(c.n); out = __added; }
    if (c.fn === 'one') { adminEvalRunOne('R1', c.qid, 1); const h = Object.keys(c.head); out = {}; h.forEach((k, i) => out[k] = __row[i]); }
    if (c.fn === 'summary') { const r = adminEvalSummary('R1'); out = { all: r.all, byType: r.byType, bySource: r.bySource, agreement: r.agreement }; }
    return { out: out, prompts: __p }; };`, sb);
process.stdout.write(JSON.stringify(cases.map((c) => sb.__run(c))));
