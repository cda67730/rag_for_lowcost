// 從 apps-script/Setup.gs 的 SETTINGS_DEFAULT 產生 docs/settings.md（兩版共用的設定參考）
// 用法：node tools/gen-settings-doc.js
const fs = require("fs"), vm = require("vm"), path = require("path");
const root = path.join(__dirname, "..");
const sb = {}; vm.createContext(sb);
vm.runInContext(fs.readFileSync(path.join(root, "apps-script/Setup.gs"), "utf8").replace("function setup()", "function setupX()") + ";this.SD=SETTINGS_DEFAULT;this.TI=TIERS;", sb);
const SD = sb.SD, TI = sb.TI, byKey = {}; SD.forEach((r) => (byKey[r[0]] = r));

// 分組與後台「機器人設定」一致；where＝Python 版存放位置（bot＝Bot 設定、flow＝可被 Flow 覆蓋、kb＝知識庫屬性、server＝伺服器）
const GROUPS = [
  ["版本與基本", ["tier", "bot_name", "welcome", "default_skill", "show_sources", "enable_invite", "daily_limit", "hourly_limit"]],
  ["角色用詞", ["domain_desc", "role_user", "role_subject", "role_escalate"]],
  ["模型", ["model_answer", "model_triage", "model_rerank", "model_check", "model_embed", "embed_dim", "temperature", "model_eval_gen", "model_eval_judge"]],
  ["流程開關", ["enable_triage", "max_followups", "enable_case_flow", "full_answer_rounds", "enable_rerank", "enable_kg", "kg_mode", "enable_deid", "deid_mode", "deid_labels", "enable_citation", "knowledge_only", "enable_answer_schema"]],
  ["檢查", ["enable_llm_check", "check_items", "check_severe", "enable_relevance_check"]],
  ["提示詞與回答格式", ["system_prompt", "answer_schema", "answer_rules", "fallback_rules", "intents", "triage_rules", "triage_focus_hint", "followup_prompt", "continue_prompt", "continue_options"]],
  ["檢索", ["top_k", "min_score", "rerank_pool", "quota_min_actionable", "quota_max_principle", "kg_boost", "kg_max_nodes"]],
  ["知識庫處理", ["chunk_size", "chunk_overlap", "enable_node_mapping"]],
  ["聊天介面文字", ["ui_gate_text", "ui_input_placeholder", "ui_waiting", "ui_referral_title", "ui_referral_note", "ui_referral_heading", "ui_ai_tag", "ui_fb_up", "ui_fb_down", "ui_accent", "msg_no_knowledge", "msg_check_failed", "msg_ai_fallback", "msg_out_of_scope"]],
];
const WHERE = { model_embed: "kb", embed_dim: "kb", chunk_size: "kb", chunk_overlap: "kb", enable_node_mapping: "kb" };
const FLOW = new Set(["tier", "enable_triage", "max_followups", "enable_case_flow", "full_answer_rounds", "enable_rerank", "enable_kg", "kg_mode", "enable_citation", "knowledge_only", "enable_answer_schema", "enable_llm_check", "enable_relevance_check", "model_answer", "model_triage", "model_rerank", "model_check", "temperature", "top_k", "min_score", "rerank_pool", "quota_min_actionable", "quota_max_principle", "kg_boost", "kg_max_nodes", "system_prompt", "answer_schema", "answer_rules", "fallback_rules", "intents", "triage_rules", "triage_focus_hint", "followup_prompt", "continue_prompt", "continue_options", "check_items", "check_severe"]);
const whereOf = (k) => WHERE[k] === "kb" ? "知識庫" : FLOW.has(k) ? "Bot（Flow 可覆蓋）" : "Bot";
const esc = (s) => String(s).replace(/\|/g, "\\|").replace(/\n/g, " ");
const short = (v) => { if (typeof v === "boolean") return v ? "開" : "關"; const s = String(v); if (s === "") return "（空）"; return s.length > 40 || /\n/.test(s) ? "（長文字，見下方）" : "`" + esc(s) + "`"; };

const used = new Set();
let out = `# 設定參考

> 本檔由 \`tools/gen-settings-doc.js\` 依 \`apps-script/Setup.gs\` 的 \`SETTINGS_DEFAULT\` 產生，請改那邊再重新產生，不要手改。

兩個後端讀同一組設定 key。Apps Script 版存在「設定」工作表；Python 版存在 \`bot_settings\`（標「Flow 可覆蓋」的可在 \`flow_settings\` 另設），標「知識庫」的在 Python 版是知識庫的屬性，建庫時決定。

- 布林值：試算表裡填 \`TRUE\`／\`FALSE\`；後台是開關。
- 文字設定裡的 \`{user}\` \`{subject}\` \`{escalate}\` \`{domain}\` 會換成「角色用詞」。
- 留空＝用程式內建預設，不會報錯。
- 回答欄位、意圖選項、檢查項目在後台有表格與「用說的」編輯器，不需要手寫格式。

## 版本

選版本＝一次寫入下表這幾個開關；之後可個別調整。

| 設定 | ${Object.keys(TI).map((k) => TI[k].name + "（`" + k + "`）").join(" | ")} |
| --- | ${Object.keys(TI).map(() => "---").join(" | ")} |
`;
const tierKeys = Object.keys(TI[Object.keys(TI)[0]].set);
tierKeys.forEach((k) => { out += `| \`${k}\` | ${Object.keys(TI).map((t) => short(TI[t].set[k])).join(" | ")} |\n`; });
out += "\n" + Object.keys(TI).map((t) => `- **${TI[t].name}**：${TI[t].desc}`).join("\n") + "\n";

const longs = [];
GROUPS.forEach(([title, keys]) => {
  out += `\n## ${title}\n\n| key | 預設 | Python 版位置 | 說明 |\n| --- | --- | --- | --- |\n`;
  keys.forEach((k) => {
    const r = byKey[k]; if (!r) return; used.add(k);
    out += `| \`${k}\` | ${short(r[1])} | ${whereOf(k)} | ${esc(r[2])} |\n`;
    if (short(r[1]) === "（長文字，見下方）") longs.push([k, r[1]]);
  });
});
const rest = SD.map((r) => r[0]).filter((k) => !used.has(k));
if (rest.length) {
  out += `\n## 其他\n\n| key | 預設 | Python 版位置 | 說明 |\n| --- | --- | --- | --- |\n`;
  rest.forEach((k) => { const r = byKey[k]; out += `| \`${k}\` | ${short(r[1])} | ${whereOf(k)} | ${esc(r[2])} |\n`; if (short(r[1]) === "（長文字，見下方）") longs.push([k, r[1]]); });
}
out += `\n## 長文字設定的預設值\n`;
longs.forEach(([k, v]) => { out += `\n### \`${k}\`\n\n\`\`\`\n${String(v)}\n\`\`\`\n`; });
out += `\n## Python 版另外的設定位置\n\n這些不是 Bot 設定 key，在 Python 版另有地方：\n
| 項目 | 位置 |
| --- | --- |
| 知識庫掛載（標籤、最少段數、權重、啟用） | \`bot_knowledge\`，Bot 後台「知識庫」分頁 |
| 知識庫可見範圍 | \`knowledge_bases.visibility\` ＋ \`kb_allowed_bots\` |
| 模型供應商金鑰、資料庫連線、JWT 密鑰、CORS、Langfuse | 伺服器環境變數（見 \`server/README.md\`） |
`;
fs.writeFileSync(path.join(root, "docs/settings.md"), out);
console.log("docs/settings.md：", SD.length, "項設定，", longs.length, "項長文字");
