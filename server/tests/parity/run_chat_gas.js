// 用 apps-script 原版 runChat_ 跑整個回合：模型、向量、檢索結果由測試給定（與 Python 端相同），其餘邏輯都是原版
const fs = require("fs"), vm = require("vm"), path = require("path");
const gas = path.join(__dirname, "..", "..", "..", "apps-script");
const read = (f) => fs.readFileSync(path.join(gas, f), "utf8");
const src = ["Setup.gs", "Store.gs", "Llm.gs", "Knowledge.gs", "Graph.gs", "Chat.gs"].map(read).join("\n").replace("function setup()", "function setupX__()");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const sb = { console: { warn() {}, log() {} } }; vm.createContext(sb);
vm.runInContext(src + `
  const __base = {}; SETTINGS_DEFAULT.forEach(r => __base[r[0]] = r[1]);
  let __c = null, __resp = null, __calls = [];
  function cfgAll_() { return __c.cfg; } function cfg_(k) { return __c.cfg[k]; }
  function skills_() { return __c.skills; } function referral_() { return __c.refs; } function forbidden_() { return __c.forbidden; } function synonyms_() { return []; }
  function logTurn_() {}
  const KIND = [['triage', '的分流器'], ['rerank', '挑選參考資料'], ['check', '回答的審查器'], ['followup', '{"empathy":"","questions"'], ['deid', '去除個人資訊']];
  function __next(p, model, o) { const k = (KIND.find(x => p.indexOf(x[1]) >= 0) || ['answer'])[0]; o = o || {}; __calls.push([k, model, o.temperature == null ? null : o.temperature, !!o.noThink]);
    const q = __resp[k]; let r = Array.isArray(q) ? (q.length > 1 ? q.shift() : q[0]) : q;
    if (r && r.__throw) throw new Error(r.__throw); return JSON.parse(JSON.stringify(r)); }
  genJson_ = function (model, prompt, o) { return __next(prompt, model, o); };
  genReq_ = function (model, parts, o) { return { prompt: parts[0].text, model: model, o: o }; };
  embedReqs_ = function () { return [{}]; }; embedParse_ = function () { return [[1], [1]]; };
  genFix_ = function (req, res) { return { v: __next(req.prompt, req.model, req.o) }; }; genText_ = function (res) { return JSON.stringify(res.v); };
  UrlFetchApp = { fetchAll: (rs) => rs.map(() => ({})), fetch: () => ({}) };
  reqArgs_ = function () { return ['', {}]; };
  searchDetail_ = function () { return JSON.parse(JSON.stringify(__c.search)); };
  this.__run = (c) => { __c = c; __c.cfg = Object.assign({}, __base, c.cfg); __resp = JSON.parse(JSON.stringify(c.resp)); __calls = []; SCHEMA_ = null;
    const r = runChat_(c.req, { trace: true, noLog: true }); return { r: r, calls: __calls }; };`, sb);
process.stdout.write(JSON.stringify(cases.map((c) => sb.__run(c))));
