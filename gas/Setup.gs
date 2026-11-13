/** 第一次使用：建立工作表與通用預設設定（已有資料的工作表不會被覆蓋）。領域內容（Skill、轉介資源、同義詞、禁止用語、提示詞）用後台「載入專案範本」匯入 */

const SYSTEM_PROMPT_DEFAULT = [
  '你是{domain}的助手，協助{user}處理工作上遇到的問題。',
  '【核心原則】',
  '- 只根據提供的知識給建議，每條建議標註知識編號（K1、K2…）',
  '- 知識不足以回答時，直接說明，並建議可以詢問的專業人員',
  '- 先理解情境再給建議；建議要少而可立即執行',
  '【回答通則】',
  '- 使用台灣繁體中文，語氣具體、不說教',
  '- 依載入的 Skill 決定流程與輸出內容',
].join('\n');

// 預設不建任何 Skill、轉介資源、同義詞、禁止用語；內容由後台「其他設定 → 載入專案範本」匯入（repo 的 templates/ 有範例）
// ---------- 可在後台修改的文字（程式只負責格式與流程，內容都在這裡和「設定」工作表） ----------
const TEXT_DEFAULTS = {
  // 回答欄位：一個欄位一行。type：text（一段文字）、list（條列）、items（有出處的建議清單：title／how／refs）
  // modes：full（完整回答）、continue（延續回覆）、both。label 是顯示標題（空＝直接顯示內文）、label_continue 延續時的標題
  // required＝模型漏填會記為格式問題；summary＝放進對話歷史摘要；copy＝前端給「複製」按鈕；count／count_continue＝items 的數量範圍
  // 系統自己管的欄位不用列：refs、knowledge_gap、referral、options、notice
  answer_schema: [
    '[',
    '{"key":"reply","type":"text","label":"","hint":"直接回應{user}的問題（兩三句）","modes":"both","required":true,"summary":true},',
    '{"key":"steps","type":"items","label":"建議做法","label_continue":"可以再試試","hint":"做法名稱／具體怎麼做（一兩句）","modes":"both","required":true,"summary":true,"count":"1-3","count_continue":"0-2"},',
    '{"key":"notes","type":"list","label":"補充說明","hint":"要留意的事項","modes":"both"}',
    ']',
  ].join('\n'),
  intents: [
    '新問題＝換了一個完全不同的狀況或主題',
    '補充同一問題＝回答追問或補充細節、說試過的結果',
    '要求特定協助＝要範本、步驟清單、其他做法、找專人等具體東西',
    '與主題無關＝閒聊、算數、與本系統服務範圍無關',
  ].join('\n'),
  triage_focus_hint: '例如「某個流程的具體步驟」「怎麼跟對方說明」',
  fallback_rules: '只能提供低風險、一般常識範圍內的做法；不得下專業判斷；來自一般知識的建議 refs 填空陣列；最後建議{user}向{escalate}或專業人員確認。',
  triage_rules: [
    '轉介等級：',
    '- 緊急：現在就要找人，例如正在發生的安全或法律風險',
    '- 需要專人：這幾天內要找人，例如做法試了很久都無效、爭議升高',
    '- 建議諮詢：AI 可以回答，專人能幫更多',
    '服務範圍：與本系統的知識領域無關，算超出範圍。',
    '資訊足夠：至少知道問題發生的情境或經過其中一項；非常籠統時算不足。',
  ].join('\n'),
  followup_prompt: '{user}的描述資訊不足。請先用一句話回應，再提出最多 2 個最關鍵、好回答的追問。',
  answer_rules: '有出處的建議 1–3 個；每個建議的 refs 只能用上面提供的 K 編號，至少一個；不得下專業判斷或使用標籤化用語。',
  check_items: [
    '依據＝每個建議是否大致能從它引用的知識推得；合理的延伸不算編造，只有知識完全沒提到、或與知識矛盾才算問題。',
    '轉介＝{user}描述中有明確需要專人的訊號（安全、法律、長期無效）而回答完全沒提。',
    '語氣＝說教、責怪{user}或{subject}、或下專業判斷。',
  ].join('\n'),
  check_severe: '編造或違反知識、遺漏需要專人的訊號、下專業判斷。其他都算小問題。',
  continue_prompt: [
    '{user}已經拿到完整建議了，這次不要再重複完整格式。',
    '- reply：用兩三句話直接回應{user}這次說的（新的狀況、試過的結果、或要求），像同事對話一樣自然',
    '- {user}說某個做法有效或無效時，針對這點調整；有新的知識可用時，最多再給 1–2 個「之前沒給過」的做法',
    '- {user}要求特定東西時，只做那一件事，放在對應欄位',
    '- 最後提供下一步選項，讓{user}選要往哪個方向走',
  ].join('\n'),
  continue_options: ['換一個不同的做法', '給我更詳細的步驟', '這個情況需要找誰協助？'].join('\n'),
  msg_no_knowledge: '謝謝你說明這個狀況。目前知識庫裡沒有足夠、經過檢查的內容可以給你具體建議。建議先和{who}討論。',
  msg_check_failed: '謝謝你說明這個狀況。我整理出的建議沒有通過依據檢查，為了避免給你不夠可靠的內容，這次先不提供具體做法。建議先和{who}討論。',
  msg_ai_fallback: '目前知識庫內容不足，以下由 AI 協助補足，尚未經審核，請斟酌使用；必要時請和{escalate}確認。',
  msg_out_of_scope: '這個問題超出本系統的服務範圍，建議洽詢相關人員。',
};
/** 讀文字設定；設定表沒有這一項時用預設值。{user}{subject}{escalate}{domain} 會換成「角色用詞」設定 */
function text_(key) {
  const v = cfg_(key);
  return tpl_(v == null || String(v).trim() === '' ? (TEXT_DEFAULTS[key] || '') : String(v));
}
// ---------- 版本：一組開關的預設組合；套用時只改這些 key，其他設定不動 ----------
const TIERS = {
  basic: { name: '基本', desc: '問答＋知識庫＋出處。單一 Skill（預設 Skill 一律載入），不分流、不追問、每次完整回答', set: { enable_triage: false, enable_case_flow: false, enable_rerank: false, enable_kg: false, max_followups: 0 } },
  multi: { name: '多取向分流', desc: '＋多個 Skill、L4 分流（意圖、主題、轉介分級）、資訊不足時追問', set: { enable_triage: true, enable_case_flow: false, enable_rerank: false, enable_kg: false, max_followups: 1 } },
  full: { name: '全功能', desc: '＋個案機制（延續回覆）、AI 重排與配額、知識圖譜參與回答', set: { enable_triage: true, enable_case_flow: true, enable_rerank: true, enable_kg: true, max_followups: 1 } },
};
/** 聊天頁用的文字與外觀設定（不含敏感內容，邀請碼驗證前就可以拿） */
function uiConfig_() {
  const C = cfgAll_(), o = { botName: String(C.bot_name || ''), welcome: String(C.welcome || '') };
  Object.keys(C).forEach(k => { if (/^ui_/.test(k) && C[k] != null && String(C[k]).trim() !== '') o[k.slice(3)] = String(C[k]); });
  return o;
}
// ---------- 角色用詞：提示詞骨架裡的稱呼都從設定來，換案子只要改這幾項 ----------
const ROLE_DEFAULTS = { domain: 'AI 知識問答系統', user: '使用者', subject: '對方', escalate: '主管' };
function roles_() {
  const C = cfgAll_(), o = {};
  ['domain', 'user', 'subject', 'escalate'].forEach(k => { const v = C[k === 'domain' ? 'domain_desc' : 'role_' + k]; o[k] = v == null || String(v).trim() === '' ? ROLE_DEFAULTS[k] : String(v).trim(); });
  return o;
}
function tpl_(s) {
  const R = roles_();
  return String(s).replace(/\{(user|subject|escalate|domain)\}/g, (m, k) => R[k]);
}
// ---------- 意圖選項：一行一個「名稱＝說明」，順序固定對應 new／more／specific／off 四種流程處理 ----------
const INTENT_KEYS = ['new', 'more', 'specific', 'off'];
function intents_() {
  const lines = text_('intents').split(/\n+/).map(l => l.trim()).filter(Boolean);
  const def = TEXT_DEFAULTS.intents.split('\n');
  return INTENT_KEYS.map((k, i) => {
    const p = String(lines[i] || def[i]).split(/[＝=]/);
    return { key: k, name: p[0].trim(), desc: p.slice(1).join('＝').trim() };
  });
}
/** L5 檢查項目：[{name, desc}]；行格式錯誤或留空時用預設 */
function checkItems_() {
  const parse = t => t.split(/\n+/).map(l => l.trim()).filter(Boolean).map(l => { const p = l.split(/[＝=]/); return { name: p[0].trim(), desc: p.slice(1).join('＝').trim() }; }).filter(x => x.name);
  const items = parse(text_('check_items'));
  return items.length ? items : parse(tpl_(TEXT_DEFAULTS.check_items));
}
function intentName_(key) { return (intents_().find(i => i.key === key) || {}).name || key; }
function intentKey_(name) { return (intents_().find(i => i.name === String(name).trim()) || {}).key || null; }

const SETTINGS_DEFAULT = [
    ['tier', 'basic', '版本：basic＝基本（問答＋知識庫，單一 Skill）、multi＝多取向分流（＋多 Skill、L4 分流與追問）、full＝全功能（＋個案機制、重排與配額、知識圖譜）。選版本會一次套用一組開關，之後每個開關仍可個別調整'],
    ['enable_invite', true, '邀請碼：開＝聊天頁要輸入邀請碼才能用（依「邀請碼」表驗證）；關＝任何人拿到網址就能用，紀錄的單位寫「公開」。關閉時請設好每日上限'],
    ['daily_limit', 500, '整個機器人每天的回答次數上限（所有使用者合計），超過回覆「今日額度已用完」。邀請碼關閉時尤其重要'],
    ['bot_name', 'AI 助手', '聊天頁顯示的名稱'],
    ['welcome', '你好，說說你遇到的狀況，我會先了解情境，再依知識庫給你建議。', '聊天頁的歡迎訊息'],
    ['ui_gate_text', '請輸入邀請碼。對話內容會保存，用於改善服務。', '聊天頁：邀請碼頁的說明文字'],
    ['ui_input_placeholder', '輸入你的問題…', '聊天頁：輸入框的提示文字'],
    ['ui_waiting', '正在了解狀況、查找資料…', '聊天頁：送出後等待回答時顯示的字'],
    ['ui_referral_title', '這個情況需要馬上找專人', '聊天頁：緊急轉介卡片的標題'],
    ['ui_referral_note', '是否進一步處理，由你和{escalate}決定。', '聊天頁：緊急轉介卡片底部的說明'],
    ['ui_referral_heading', '需要時找專人', '聊天頁：回答裡轉介建議的小標題'],
    ['ui_ai_tag', 'AI 補充', '聊天頁：知識不足由 AI 補足的建議旁邊的標籤'],
    ['ui_fb_up', '👍 有幫助', '聊天頁：正面回饋按鈕文字（統計以「有幫助」三個字比對）'],
    ['ui_fb_down', '👎 沒幫助', '聊天頁：負面回饋按鈕文字（統計以「沒幫助」三個字比對）'],
    ['ui_accent', '', '聊天頁：主色（十六進位色碼，例如 #1e7f7d）；留空用預設'],
    ['system_prompt', SYSTEM_PROMPT_DEFAULT, '固定 system prompt（第 1 層）'],
    ['domain_desc', 'AI 知識問答系統', '領域描述：分流器與審查器的 prompt 開頭「你是〔領域描述〕的分流器」'],
    ['role_user', '使用者', '提問者的稱呼；所有 prompt 裡的 {user} 都換成這個'],
    ['role_subject', '對方', '談論對象的稱呼（提問者在問誰的事）；{subject}'],
    ['role_escalate', '主管', '預設升級對象：轉介資源沒有「系統能力不足」時，保守回答建議找誰；{escalate}'],
    ['intents', TEXT_DEFAULTS.intents, 'L4 意圖選項，一行一個「名稱＝說明」，順序固定：1 新問題（只看這一則）、2 補充同一問題、3 要求特定協助（不追問、延續回覆）、4 與主題無關（拒答）。只改名稱與說明，不要增減行'],
    ['triage_focus_hint', TEXT_DEFAULTS.triage_focus_hint, 'L4 判斷「提問焦點」時給模型看的範例'],
    ['fallback_rules', TEXT_DEFAULTS.fallback_rules, 'knowledge_only 關閉、由 AI 補足時的限制（生成與 L5 檢查都用這段）'],
    ['enable_answer_schema', true, '回答欄位：開＝回答依「回答欄位」設定分成固定幾段（標題、建議清單…）；關＝自由作答，一段文字加可選的參考做法，不強制格式（FAQ 型或想讓模型自然回答時用）'],
    ['answer_schema', TEXT_DEFAULTS.answer_schema, '回答欄位（JSON，一欄一行）：key／type（text、list、items）／label／hint／modes（full、continue、both）／required／summary／copy；items 另有 count、count_continue。至少要有一個 items 欄位（出處檢查用）。格式錯誤時會用預設欄位'],
    ['model_triage', 'gemini-2.5-flash', 'L4 分流用模型'],
    ['model_answer', 'gemini-2.5-flash', 'A1 對話 Agent 用模型（也用於 PDF 轉文字）'],
    ['model_check', 'gemini-2.5-flash', 'L5 檢查用模型'],
    ['model_rerank', 'gemini-2.5-flash-lite', '重新排序用模型（只打 0–3 分，用較快較省的模型即可；判斷變差可改回 gemini-2.5-flash）'],
    ['model_embed', 'gemini-embedding-001', '向量模型（更換後要重新向量化所有文件）'],
    ['embed_dim', 768, '向量維度'],
    ['temperature', 0.3, '生成溫度（只對 Gemini 2.5 以前的模型有效；Gemini 3 以上官方建議維持預設 1.0，系統不送溫度）'],
    ['top_k', 6, '每次檢索取幾段'],
    ['min_score', 0.45, '相似度門檻，低於此值不採用'],
    ['chunk_size', 500, '切段長度（字）'],
    ['chunk_overlap', 80, '段落重疊（字）'],
    ['max_followups', 1, '最多追問幾輪'],
    ['enable_deid', false, 'P03 去識別化：開＝把訊息裡的名字換成代稱、刪手機號碼後才送模型與寫紀錄（規則是幼教雛形的「小＋一字」）；關＝原文直接使用。關閉時請自行注意紀錄裡的個資'],
    ['enable_citation', true, '出處機制：開＝回答裡的建議（items 欄位）每條都要引用知識編號，P09 核對出處與數量；關＝不要求引用、不做 P09，回答欄位可以只有文字（FAQ 型或不需逐條溯源的專案用）'],
    ['deid_mode', 'ai', '去識別化方式：ai＝用模型找出人名、小名、綽號、機構名換成代稱（L4 分流開著時併在同一次呼叫，不多花時間；L4 關閉時另外呼叫一次；失敗時自動改用規則）；rule＝只用規則（「小＋一字」）。兩者都會刪電話、Email、證號'],
    ['deid_labels', '', '代稱前綴（通常不用填）：留空＝談論對象用「談論對象稱呼」、人員用「提問者稱呼」、家人／某機構／某人固定。要改才填，一行一個「類別＝代稱」，類別：subject、family、staff、org、other'],
    ['enable_case_flow', true, '個案機制：開＝L4 判斷是否換了新個案、同一個案給 full_answer_rounds 次完整回答後改延續回覆；關＝整段對話都帶進去、每次都完整回答，不切換延續回覆（FAQ 型專案用）'],
    ['enable_triage', true, 'L4 分流：開＝AI 判斷意圖、焦點、轉介、主題、是否追問；關＝不呼叫分流 AI，主題用關鍵字比對，直接檢索知識庫回答（較快較省，但不會偵測緊急轉介、不追問、不判斷超出範圍）'],
    ['enable_llm_check', true, 'L5 AI 檢查：開＝回答後由 AI 依「檢查項目」逐項檢查；關＝只做程式檢查（禁止用語、出處編號）'],
    ['knowledge_only', true, '只用知識庫回答：開＝知識不足時保守回答；關＝知識不足時由 AI 協助補足（照樣檢查，並標示「知識庫內容不足」）'],
    ['enable_relevance_check', true, '是否檢查回答有沒有直接回應提問焦點（L5 關閉時也可單獨啟用）'],
    ['enable_rerank', true, '是否用 AI 依「能不能回答提問焦點」重新排序檢索結果（每題多一次 AI 呼叫）'],
    ['model_eval_gen', '', '自動評測出題用模型；留空＝用檢查模型。建議用和回答不同家的模型，避免自己出題自己答'],
    ['model_eval_judge', '', '自動評測評分用模型；留空＝用檢查模型。建議用和回答不同家的模型，避免自己改自己的考卷'],
    ['enable_node_mapping', false, '上傳文件時就標註（預設關閉：先累積文件，之後在「知識圖譜」分頁一次標註）。開啟時，AI 照 ontology 的標註規格標每一段（主要／次要節點、讀者對象、適用年齡、情境），並提出新節點候選（需先在後台匯入知識圖譜；每份文件多幾次 AI 呼叫）'],
    ['rerank_pool', 12, '重新排序前先取幾段候選'],
    ['enable_kg', false, '知識圖譜參與回答：開＝依問題比對圖譜節點（名稱、同義說法），沿關係擴一層，節點定義與實務要點放進回答 prompt 當背景；需要先在「知識圖譜」分頁匯入圖譜並標註段落'],
    ['kg_mode', 'hybrid', '圖譜怎麼影響檢索：hybrid＝標到節點的段落加分，其他段落照常；only＝只用標到節點的段落，沒對到任何節點就視為沒有可用知識'],
    ['kg_boost', 0.08, 'hybrid 模式下，標到命中節點（主要標籤）的段落加多少分；次要標籤與鄰居節點加一半'],
    ['kg_max_nodes', 3, '每題最多取幾個直接命中的節點（每個再帶最多 3 個相鄰節點）'],
    ['quota_min_actionable', 3, '採用的段落中，至少保留幾段「具體做法／案例／溝通範例」'],
    ['quota_max_principle', 2, '採用的段落中，「原則／背景」最多幾段'],
    ['default_skill', '', '沒有對應主題時預設載入的 Skill；留空＝不載入主題 Skill，依問題直接回答（「一律載入」的 Skill 仍會載入）'],
    ['hourly_limit', 300, '每個邀請碼每小時呼叫上限'],
    ['triage_rules', TEXT_DEFAULTS.triage_rules, 'L4 分流規則：轉介等級怎麼判斷、什麼不算、資訊足夠的標準'],
    ['followup_prompt', TEXT_DEFAULTS.followup_prompt, '資訊不足時，追問要怎麼問'],
    ['answer_rules', TEXT_DEFAULTS.answer_rules, '回答的硬性規則（策略數量、出處、用語）'],
    ['check_items', TEXT_DEFAULTS.check_items, 'L5 檢查項目，一行一項「類型＝判斷說明」；可增減。類型名稱會出現在檢查結果與問題統計'],
    ['check_severe', TEXT_DEFAULTS.check_severe, 'L5 什麼算嚴重（嚴重才會擋下回答並重寫，其他只記錄）'],
    ['full_answer_rounds', 2, '同一個個案給幾次完整回答；之後改成延續回覆（只回應新內容＋下一步選項）'],
    ['continue_prompt', TEXT_DEFAULTS.continue_prompt, '延續回覆要怎麼回'],
    ['continue_options', TEXT_DEFAULTS.continue_options, '延續回覆提供的下一步選項（一行一個，AI 會挑適合的）'],
    ['msg_no_knowledge', TEXT_DEFAULTS.msg_no_knowledge, '知識庫查不到資料時的回覆；{who} 會換成轉介資源「系統能力不足」的對象'],
    ['msg_check_failed', TEXT_DEFAULTS.msg_check_failed, '回答沒通過檢查時的回覆；{who} 同上'],
    ['msg_ai_fallback', TEXT_DEFAULTS.msg_ai_fallback, '知識不足、由 AI 補足時，放在回答最上方的提醒'],
    ['msg_out_of_scope', TEXT_DEFAULTS.msg_out_of_scope, '超出服務範圍時的回覆'],
];

/** 設定表已存在時，只補上缺少的設定項，不動已修改的值 */
function addMissingSettings_() {
  const s = sh_('settings');
  const have = s.getRange(1, 1, s.getLastRow(), 1).getValues().map(r => r[0]);
  const add = SETTINGS_DEFAULT.filter(r => have.indexOf(r[0]) < 0);
  if (add.length) s.getRange(s.getLastRow() + 1, 1, add.length, 3).setValues(safeRows_(add));
}

function setup() {
  // 只能從 Apps Script 編輯器或試算表選單執行（網頁上的匿名呼叫拿不到帳號，直接拒絕）
  if (!Session.getActiveUser().getEmail()) throw new Error('請從 Apps Script 編輯器或試算表選單執行 setup()');
  const ss = ss_();
  const make = (name, head, seed) => {
    let s = ss.getSheetByName(name);
    if (!s) s = ss.insertSheet(name);
    if (s.getLastRow() === 0) {
      s.appendRow(head); s.setFrozenRows(1); s.getRange(1, 1, 1, head.length).setFontWeight('bold');
      if (seed && seed.length) s.getRange(2, 1, seed.length, head.length).setValues(safeRows_(seed));
    } else {
      // 舊版工作表補上新欄位：對話紀錄是依位置寫入，直接換成新標題；其他表把缺的欄位加在最後
      const cur0 = s.getRange(1, 1, 1, s.getLastColumn()).getValues()[0];
      const cur = cur0.map(h => HEADER_ALIAS[h] || h);
      if (cur.some((h, i) => h !== cur0[i])) s.getRange(1, 1, 1, cur.length).setValues([cur]);
      const missing = head.filter(h => cur.indexOf(h) < 0);
      if (missing.length) {
        if (name === SHEETS.logs) s.getRange(1, 1, 1, head.length).setValues([head]).setFontWeight('bold');
        else s.getRange(1, cur.length + 1, 1, missing.length).setValues([missing]).setFontWeight('bold');
      }
    }
    return s;
  };
  make(SHEETS.settings, ['設定', '值', '說明'], SETTINGS_DEFAULT);
  addMissingSettings_();
  make(SHEETS.skills, ['Skill ID', '名稱', '對應主題', '一律載入', '內容', '啟用', '版本', '更新者', '更新時間']);
  make(SHEETS.skillHist, ['Skill ID', '版本', '名稱', '對應主題', '一律載入', '內容', '更新者', '更新時間']);
  make(SHEETS.docs, ['文件ID', '標題', '出處', '證據等級', '原始檔名', '段落數', '狀態', '上傳者', '上傳時間', '審核者', '審核時間']);
  make(SHEETS.chunks, ['段落ID', '文件ID', '序號', '內容', '頁碼', '向量', '狀態', '類型', '讀者對象', '適用年齡', '情境']);
  make(SHEETS.synonyms, ['使用者用語', '對應概念']);
  make(SHEETS.forbidden, ['詞或規則', '類型', '說明']);
  make(SHEETS.referral, ['類別', '該找誰', '聯絡方式', '現在要做的事']);
  // 測試邀請碼每次隨機產生（不寫在程式裡，避免公開在 GitHub）
  const testCode = 'TEST-' + Utilities.getUuid().replace(/-/g, '').slice(0, 10).toUpperCase();
  const codesNew = !ss.getSheetByName(SHEETS.codes) || ss.getSheetByName(SHEETS.codes).getLastRow() === 0;
  make(SHEETS.codes, ['邀請碼', '單位代號', '啟用', '觀測'], [[testCode, '測試單位', true, true]]);
  if (codesNew) console.log('已建立測試邀請碼：' + testCode + '（在「邀請碼」工作表，正式使用前請停用）');
  const me = Session.getActiveUser().getEmail();
  make(SHEETS.admins, ['Email', '備註', '帳號', '密碼雜湊', '鹽'], me ? [[me, '建立者', '', '', '']] : []);
  make(SHEETS.logs, ['時間', 'Session', '單位', '輪次', '使用者訊息', '分流', '轉介', '回應類型', '回答', '引用', '檢查', '是否重寫', '耗時ms', '檢索段數', '最高相似度', '被引用段數', '載入Skill', '流程紀錄', '穩定度', '品質問題']);
  make(SHEETS.feedback, ['時間', 'Session', '輪次', '類型', '備註']);
  make(SHEETS.kgNodes, ['節點ID', '名稱', '類型', '上層', '路徑', '定義', '實務要點', '來源ID', '同義說法', '狀態', '來源', '新增時間']);
  make(SHEETS.kgEdges, ['主詞', '關係', '受詞', '關係說明', '來源']);
  make(SHEETS.kgSources, ['來源ID', '標題', '出版單位', '網址', '範圍']);
  make(SHEETS.kgLinks, ['段落ID', '節點ID', '方式', '時間', '標籤']);
  make(SHEETS.kgDefs, ['種類', 'ID', '名稱', '定義'], kgDefsSeed_());   // 只在新建時寫入預設；已有的表不動
  make(SHEETS.kgCands, ['候選ID', '名稱', '定義', '建議上層', '類型', '建議關係', '文件ID', '段落ID', '次數', '理由', '狀態', '處理結果', '審核者', '審核時間']);
  make(SHEETS.evalQs, ['題目ID', '來源', '類型', '題目', '應命中段落ID', '應命中文件ID', '應包含重點', '應轉介', '期望回應', '狀態', '專家確認', '建立時間']);
  make(SHEETS.evalRuns, ['批次ID', '時間', '題目ID', '來源', '類型', '第幾次', '回應類型', '找對段落', '名次', '對題', '涵蓋重點', '依據', '安全', '安全說明', '格式問題', '回答模型', '評分模型', '評分說明', '回答摘要', '耗時ms', '專家抽查', '專家意見']);
  make(SHEETS.evalset, ['測試問題', '應命中文件ID', '備註', '結果', '名次', '最高分', '命中段落', '測試時間']);
  const def = ss.getSheetByName('工作表1') || ss.getSheetByName('Sheet1');
  if (def && def.getLastRow() === 0 && ss.getSheets().length > 1) ss.deleteSheet(def);
  clearCfgCache_();
}
