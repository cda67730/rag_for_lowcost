// 用 apps-script 的 rerankSelect_ 跑：genJson_ 換成回傳預先指定的結果（或丟錯），擷取送給模型的 prompt
const fs = require("fs"), vm = require("vm"), path = require("path");
const gas = path.join(__dirname, "..", "..", "..", "apps-script");
const read = (f) => fs.readFileSync(path.join(gas, f), "utf8");
const src = read("Setup.gs").replace("function setup()", "function setupX__()") + "\n" + read("Chat.gs") + "\n" + read("Knowledge.gs");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const sb = { console: { warn() {}, log() {} }, __cfg: {}, __resp: null, __got: null }; vm.createContext(sb);
vm.runInContext(src + `
  const __base = {}; SETTINGS_DEFAULT.forEach(r => __base[r[0]] = r[1]);
  function cfgAll_() { return __cfg; } function cfg_(k) { return __cfg[k]; } function stabNote_() {} function stabAt_() {}
  function truthy_(v) { return v === true || String(v).toUpperCase() === 'TRUE' || v === '是'; }
  function genJson_(model, prompt) { __got = { model: model, prompt: prompt }; if (__resp && __resp.throw) throw new Error(__resp.throw); return __resp.value; }
  this.__run = (c) => { __cfg = Object.assign({}, __base, c.cfg || {}); __resp = c.resp; __got = null;
    const r = rerankSelect_(c.focus, c.caseText, c.pool, __cfg); return { r: r, got: __got }; };`, sb);
process.stdout.write(JSON.stringify(cases.map((c) => sb.__run(c))));
