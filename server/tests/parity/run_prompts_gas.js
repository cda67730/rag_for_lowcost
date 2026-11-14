// 用 apps-script 原版組提示詞，輸出字串給 Python 逐字比對
const fs = require("fs"), vm = require("vm"), path = require("path");
const gas = path.join(__dirname, "..", "..", "..", "apps-script");
const read = (f) => fs.readFileSync(path.join(gas, f), "utf8");
const src = read("Setup.gs").replace("function setup()", "function setupX__()") + "\n" + read("Chat.gs") + "\n" + read("Graph.gs");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const sb = { console, __cfg: {}, __forb: [] }; vm.createContext(sb);
vm.runInContext(src + `
  const __base = {}; SETTINGS_DEFAULT.forEach(r => __base[r[0]] = r[1]);
  function cfgAll_() { return __cfg; } function cfg_(k) { return __cfg[k]; } function stabNote_() {} function forbidden_() { return __forb; } function genJson_() { throw new Error('stub'); }
  function truthy_(v) { return v === true || String(v).toUpperCase() === 'TRUE' || v === '是'; }
  this.__run = (c) => { __cfg = Object.assign({}, __base, c.cfg || {}); __forb = c.forbidden || []; SCHEMA_ = null; const a = c.args || {};
    switch (c.fn) {
      case 'triage': return triagePrompt_(a.prev, a.latest, a.topics, a.cats, a.deidKnown);
      case 'skill': { const R = roles_(); const pick = a.pick; const noTopic = !pick.some(s => !s.always);   // 與 runChat_ 第 221–222 行相同
        return pick.map(s => '【' + s.name + '】\\n' + s.content).join('\\n\\n') + (noTopic ? (pick.length ? '\\n\\n' : '') + '（這題沒有對應的主題 Skill：依' + R.user + '的問題本身與檢索到的知識直接回答，不必套用特定框架。）' : ''); }
      case 'followup': { const R = roles_();   // 與 runChat_ 第 228–229 行相同
        return a.sys + '\\n\\n' + a.skills + '\\n\\n' + text_('followup_prompt') + '\\n目前缺少：' + ((a.missing || []).join('、') || '情境細節') + '\\n只輸出 JSON：{"empathy":"","questions":["",""]}\\n\\n' + R.user + '的描述：\\n' + a.caseText; }
      case 'full': return assemble_(a.sys, a.skills, a.tri, a.caseText, a.hits, a.history, a.ref, a.refWho, a.feedback, a.focus, a.fallback, a.kg);
      case 'cont': return assembleContinue_(a.sys, a.skills, a.tri, a.prev, a.latest, a.hits, a.history, a.ref, a.refWho, a.intent, a.feedback, a.focus, a.fallback, a.kg);
      case 'conservative': return conservativeMsg_(a.refTable, a.why);
      case 'options': return continueOptions_();
      case 'kg': return kgPromptText_(a.kg);
    }
  };
  this.__checksPrompt = null;`, sb);
// checks_ 的 prompt 在函式內組好就送出：攔截 genJson_ 取得 prompt
vm.runInContext(`this.__captureChecks = (c) => { __cfg = Object.assign({}, __base, c.cfg || {}); __forb = c.forbidden || []; SCHEMA_ = null; const a = c.args; let got = null;
  const orig = genJson_; genJson_ = (m, p) => { got = p; return { issues: [], relevance: { addressed: true } }; };
  try { checks_(a.caseText, a.ans, a.hits, __cfg, a.range, a.focus, a.fallback); } finally { genJson_ = orig; } return got; };`, sb);
process.stdout.write(JSON.stringify(cases.map((c) => c.fn === "checks" ? sb.__captureChecks(c) : sb.__run(c))));
