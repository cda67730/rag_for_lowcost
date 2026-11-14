// apps-script 原版 adminDescribe／describeNormalize_／adminDescribeBack
const fs = require("fs"), vm = require("vm"), path = require("path");
const gas = path.join(__dirname, "..", "..", "..", "apps-script");
const read = (f) => fs.readFileSync(path.join(gas, f), "utf8");
const src = ["Setup.gs", "Chat.gs", "Admin.gs"].map(read).join("\n").replace("function setup()", "function setupX__()");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const sb = { console }; vm.createContext(sb);
vm.runInContext(src + `
  const __base = {}; SETTINGS_DEFAULT.forEach(r => __base[r[0]] = r[1]);
  let __c = null, __p = null;
  function cfgAll_() { return __c.cfg; } function cfg_(k) { return __c.cfg[k]; } function requireAdmin_() {} function stabNote_() {}
  function truthy_(v) { return v === true || String(v).toUpperCase() === 'TRUE' || v === '是'; }
  function genJson_(m, p) { __p = p; return JSON.parse(JSON.stringify(__c.resp)); }
  this.__run = (c) => { __c = c; __c.cfg = Object.assign({}, __base, c.cfg); __p = null; SCHEMA_ = null;
    try { const out = c.back ? adminDescribeBack(c.kind) : adminDescribe(c.kind, c.text, c.current); return { out: out, prompt: __p }; }
    catch (e) { return { error: e.message, prompt: __p }; } };`, sb);
process.stdout.write(JSON.stringify(cases.map((c) => sb.__run(c))));
