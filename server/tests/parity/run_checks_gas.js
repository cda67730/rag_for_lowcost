// 用 apps-script 原版跑 validateAnswer_、cleanAnswer_、stripRefs_、checks_；模型回覆由測試給定
const fs = require("fs"), vm = require("vm"), path = require("path");
const gas = path.join(__dirname, "..", "..", "..", "apps-script");
const read = (f) => fs.readFileSync(path.join(gas, f), "utf8");
const src = read("Setup.gs").replace("function setup()", "function setupX__()") + "\n" + read("Chat.gs") + "\n" + read("Graph.gs");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const sb = { console }; vm.createContext(sb);
vm.runInContext(src + `
  const __base = {}; SETTINGS_DEFAULT.forEach(r => __base[r[0]] = r[1]);
  let __cfg = {}, __forb = [], __notes = [], __resp = null, __sent = null;
  function cfgAll_() { return __cfg; } function cfg_(k) { return __cfg[k]; } function forbidden_() { return __forb; }
  function stabNote_(k, m) { __notes.push([k, m]); }
  function genJson_(model, p, o) { __sent = { model: model, o: o }; if (__resp && __resp.__throw) throw new Error(__resp.__throw); return JSON.parse(JSON.stringify(__resp)); }
  function truthy_(v) { return v === true || String(v).toUpperCase() === 'TRUE' || v === '是'; }
  this.__run = (c) => { __cfg = Object.assign({}, __base, c.cfg || {}); __forb = c.forbidden || []; __notes = []; __resp = c.resp; __sent = null; SCHEMA_ = null; const a = c.args || {};
    let out;
    switch (c.fn) {
      case 'strip': out = a.texts.map(stripRefs_); break;
      case 'validate': validateAnswer_(a.ans, a.mode); out = null; break;
      case 'clean': out = cleanAnswer_(a.ans, a.mode); break;
      case 'checks': out = checks_(a.caseText, a.ans, a.hits, __cfg, a.range, a.focus, a.fallback); break;
    }
    return { out: out, notes: __notes.filter(n => n[0] !== '設定錯誤'), sent: __sent };
  };`, sb);
process.stdout.write(JSON.stringify(cases.map((c) => sb.__run(c))));
