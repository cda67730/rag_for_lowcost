// 設定的分組與存放位置：docs/settings.md 產生器與 server/settings/defaults.json 匯出共用這一份
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

module.exports = { GROUPS, WHERE, FLOW };
