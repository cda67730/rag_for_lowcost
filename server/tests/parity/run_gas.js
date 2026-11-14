// 用 Apps Script 原始程式（Setup.gs、Chat.gs、Store.gs 的 truthy_）跑一組設定，輸出各解析函式的結果，給 Python 測試比對
// 用法：node run_gas.js < cases.json   （每個 case 是 {"cfg": {...}, "answer": {...}}；cfg 疊在預設值上）
const fs = require("fs"), vm = require("vm"), path = require("path");
const gas = path.join(__dirname, "..", "..", "..", "apps-script");
const src = fs.readFileSync(path.join(gas, "Setup.gs"), "utf8").replace("function setup()", "function setupX__()") + "\n" +
  fs.readFileSync(path.join(gas, "Chat.gs"), "utf8") + "\n" +
  "function truthy_(v) { return v === true || String(v).toUpperCase() === 'TRUE' || v === '是'; }";
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const out = cases.map((c) => {
  const notes = [];
  const sb = { console, __cfg: null, __notes: notes };
  vm.createContext(sb);
  vm.runInContext(src + `
    const __base = {}; SETTINGS_DEFAULT.forEach(r => __base[r[0]] = r[1]);
    function cfgAll_() { return __cfg; }
    function cfg_(k) { return __cfg[k]; }
    function stabNote_(k, n) { __notes.push([k, n]); }
    this.__run = function (over, answer) {
      __cfg = Object.assign({}, __base, over); SCHEMA_ = null;
      const pub = f => ({ key: f.key, type: f.type, label: f.label, labelContinue: f.labelContinue, hint: f.hint, modes: f.modes,
        required: f.required, summary: f.summary, copy: f.copy, count: f.count, countContinue: f.countContinue });
      return {
        roles: roles_(), intents: intents_(), check_items: checkItems_(), deid_labels: deidLabels_(), ui: uiConfig_(),
        schema: schema_().map(pub), range_full: itemsRange_('full'), range_continue: itemsRange_('continue'),
        format_full: schemaFormat_('full', ['"knowledge_gap":null']), format_continue: schemaFormat_('continue', []),
        summary_full: answerSummary_(answer, 'answer'), summary_continue: answerSummary_(answer, 'continue'),
        items_n: answerItems_(answer).length,
        text: { check_items: text_('check_items'), fallback_rules: text_('fallback_rules'), msg_ai_fallback: text_('msg_ai_fallback'), welcome: text_('welcome') },
        intent_key_of_2nd: intentKey_(intents_()[1].name), intent_name_off: intentName_('off'),
        notes: __notes.slice(),
      };
    };`, sb);
  return sb.__run(c.cfg || {}, c.answer || {});
});
process.stdout.write(JSON.stringify(out));
