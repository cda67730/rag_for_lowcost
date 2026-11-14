// 把 apps-script 的預設值匯出成 server/settings/defaults.json，Python 版直接讀這份（預設值只維護在 Setup.gs 一處）
// 用法：node tools/export-defaults.js        （改了 Setup.gs 的預設值就重跑；測試會檢查是否同步）
const fs = require("fs"), vm = require("vm"), path = require("path");
const { GROUPS, WHERE, FLOW } = require("./settings-meta");
const root = path.join(__dirname, "..");
const read = (f) => fs.readFileSync(path.join(root, "apps-script", f), "utf8");

function build() {
  const sb = { cfgAll_: () => ({}), cfg_: () => undefined, console };
  vm.createContext(sb);
  vm.runInContext(read("Setup.gs").replace("function setup()", "function setupX__()") + "\n" + read("Chat.gs") +
    ";this.__={SD:SETTINGS_DEFAULT,TD:TEXT_DEFAULTS,TI:TIERS,RD:ROLE_DEFAULTS,IK:INTENT_KEYS,SF:SCHEMA_FREE};", sb);
  const x = sb.__;
  return {
    _comment: "由 tools/export-defaults.js 自 apps-script/Setup.gs 與 Chat.gs 產生，請勿手改",
    settings: x.SD.map(([key, value, note]) => ({ key, value, note })),
    text_defaults: x.TD,
    tiers: x.TI,
    role_defaults: x.RD,
    intent_keys: x.IK,
    schema_free: x.SF.map((f) => ({ key: f.key, type: f.type, label: f.label, label_continue: f.labelContinue, hint: f.hint, modes: f.modes,
      required: f.required, summary: f.summary, copy: f.copy, count: f.count.join("-"), count_continue: f.countContinue.join("-") })),
    groups: GROUPS.map(([title, keys]) => ({ title, keys })),
    flow_overridable: [...FLOW].sort(),
    kb_level: Object.keys(WHERE).filter((k) => WHERE[k] === "kb").sort(),
  };
}

const out = JSON.stringify(JSON.parse(JSON.stringify(build())), null, 2) + "\n";
const dest = path.join(root, "server/settings/defaults.json");
if (process.argv.includes("--check")) {
  const cur = fs.existsSync(dest) ? fs.readFileSync(dest, "utf8") : "";
  if (cur !== out) { console.error("server/settings/defaults.json 與 Setup.gs 不同步，請執行 node tools/export-defaults.js"); process.exit(1); }
  console.log("defaults.json 已同步");
} else {
  fs.writeFileSync(dest, out);
  const d = JSON.parse(out);
  console.log("已寫入 server/settings/defaults.json：", d.settings.length, "項設定、", Object.keys(d.text_defaults).length, "項長文字預設、", Object.keys(d.tiers).length, "個版本");
}
