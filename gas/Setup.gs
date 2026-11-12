/** 第一次使用：建立工作表與預設內容（已有資料的工作表不會被覆蓋） */

const SYSTEM_PROMPT_DEFAULT = [
  '你是陪伴學前融合教育老師的助手，協助老師面對班上有特殊需求幼兒的日常困難。',
  '【核心原則】',
  '- 以孩子此刻的需要為出發點，不推測診斷，不使用標籤化用語',
  '- 只根據提供的知識給建議，每條建議標註知識編號（K1、K2…）',
  '- 知識不足以回答時，直接說明，並建議可以詢問的專業人員',
  '- 先理解情境再給建議；建議要少而可立即執行（2–3 個）',
  '【轉介原則】',
  '- 超出你可以負責回答的範圍時，依轉介等級引導老師找到對的專人',
  '【回答通則】',
  '- 使用台灣繁體中文與幼教現場用語，語氣溫暖、具體、不說教',
  '- 先回應老師的感受，再進入建議',
  '- 依載入的 Skill 決定流程與輸出內容',
].join('\n');

const SKILLS_DEFAULT = [
  ['S01', '理解孩子', '情緒行為, 幼兒發展與參與', false,
    '何時使用：老師描述幼兒的行為、情緒、參與或學習困難。\n回答框架：\n1. 看見：把老師的描述轉成可觀察的事件（何時、何地、做了什麼）\n2. 理解：推想行為背後未被滿足的需要\n3. 支持：優先調整環境與作息\n4. 教會：教孩子用替代的方式表達\n先追問（資訊不足時最多兩題）：\n- 通常在什麼時段或情境發生？\n- 發生前發生了什麼事？之後怎麼結束？\n輸出：2–3 個立即可用的策略，列出接下來的觀察重點。\n禁止：推測診斷（如「可能是過動」）、標籤化用語（如「問題兒童」）。'],
  ['S02', '親師溝通', '親師溝通', false,
    '何時使用：牽涉家長，或老師想知道怎麼跟家長說。\n輸出：一段老師可以直接傳給家長的訊息草稿（80–150 字）：先說孩子的優點與觀察到的事實，再說明園方正在做的支持，最後邀請合作或約時間。\n禁止：診斷詞語、要求家長「管教」、責怪語氣。'],
  ['S03', '教師支持', '教師自我照顧', false,
    '何時使用：老師提到壓力、疲憊、自責、班級難以兼顧。\n輸出：肯定老師已經做的事，給一兩個具體可行的自我照顧或求援做法。\n禁止：說教、要求老師更努力。'],
  ['S04', '轉介與找專人', '', true,
    '一律啟用。依分流結果的轉介等級回應：\n- 需要專人：給暫時的安全做法，並在 referral 欄位明確說明這不能取代專人、該找誰\n- 建議諮詢：正常回答，在 referral 欄位簡短附上可以找誰\n- 無：referral 為 null\n不要把一般玩耍碰撞視為疑似受虐。'],
];

const REFERRAL_DEFAULT = [
  ['兒少保護', '園長，並依法向縣市社政主管機關通報', '113 保護專線', '依兒少權法，知悉後應立即通報，至遲不超過 24 小時；告知園長，記錄看到與聽到的事實；不要自行追問孩子細節或質問家長'],
  ['健康與醫療', '醫療人員、園內護理人員與家長', '緊急時撥 119', '先確保孩子安全，依園內緊急處理流程通知家長'],
  ['發展與特教判斷', '特教巡迴輔導老師、特教資源中心或早療評估單位', '請園長協助申請', '整理近期的觀察紀錄，作為諮詢與評估的資料'],
  ['法律與行政', '園長或行政主管', '', '保留相關紀錄與溝通內容'],
  ['親師衝突升高', '園長或主任', '', '先暫停個別溝通，請主管一起處理'],
  ['老師自身狀態', '主管、員工協助方案或心理專業人員', '安心專線 1925', '若有傷害自己的念頭，請立即撥打 1925 或告訴身邊信任的人'],
  ['系統能力不足', '園長、協同老師或專案特教團隊', '', '把狀況整理成文字，和團隊一起討論'],
];

// ---------- 可在後台修改的文字（程式只負責格式與流程，內容都在這裡和「設定」工作表） ----------
const TEXT_DEFAULTS = {
  // 回答欄位：一個欄位一行。type：text（一段文字）、list（條列）、items（有出處的建議清單：title／how／refs）
  // modes：full（完整回答）、continue（延續回覆）、both。label 是顯示標題（空＝直接顯示內文）、label_continue 延續時的標題
  // required＝模型漏填會記為格式問題；summary＝放進對話歷史摘要；copy＝前端給「複製」按鈕；count／count_continue＝items 的數量範圍
  // 系統自己管的欄位不用列：refs、knowledge_gap、referral、options、notice
  answer_schema: [
    '[',
    '{"key":"empathy","type":"text","label":"","hint":"先回應{user}感受的一兩句","modes":"full","required":true,"summary":true},',
    '{"key":"reply","type":"text","label":"","hint":"直接回應{user}這次說的話","modes":"continue","required":true,"summary":true},',
    '{"key":"understanding","type":"text","label":"看見與理解","hint":"把行為轉成可觀察事件，推想背後的需要（兩三句）","modes":"full","required":true},',
    '{"key":"strategies","type":"items","label":"現在可以試試","label_continue":"可以再試試","hint":"策略名稱／具體怎麼做（一兩句）","modes":"both","required":true,"summary":true,"count":"2-3","count_continue":"0-2"},',
    '{"key":"parent_message","type":"text","label":"給家長的訊息草稿","hint":"若牽涉家長，給家長的訊息草稿，否則 null","modes":"both","copy":true},',
    '{"key":"observe","type":"list","label":"接下來觀察記錄","label_continue":"觀察重點","hint":"接下來的觀察重點","modes":"both"},',
    '{"key":"teacher_care","type":"text","label":"也照顧你自己","hint":"若{user}提到壓力，一兩句自我照顧建議，否則 null","modes":"full"}',
    ']',
  ].join('\n'),
  intents: [
    '新個案＝換了一個{subject}或完全不同的狀況',
    '補充同一個案＝回答追問或補充細節、說試過的結果',
    '要求特定協助＝要家長訊息、觀察記錄、其他策略、找專人等具體東西',
    '與主題無關＝閒聊、算數、與帶班無關',
  ].join('\n'),
  triage_focus_hint: '例如「收玩具時不再丟積木的具體做法」「怎麼跟家長開口」',
  fallback_rules: '只能提供低風險、班級日常可做的做法（環境調整、預告、示範、正向回饋、觀察記錄等）；不得判斷行為功能、介入層級或推測診斷；來自一般知識的策略 refs 填空陣列；最後建議{user}把狀況整理給專業人員確認。',
  triage_rules: [
    '轉介等級：',
    '- 緊急：現在就要找人，例如孩子自述被大人傷害、老師有傷害自己的念頭、正在發生的醫療狀況',
    '- 需要專人：這幾天內要找人，例如策略試了很久都無效、家長衝突升高',
    '- 建議諮詢：AI 可以回答，專人能幫更多',
    '一般玩耍碰撞或跌倒的瘀青、有合理說明的小傷，不屬於兒少保護。',
    '服務範圍：要求醫療診斷或與幼教無關，算超出範圍。',
    '資訊足夠：至少知道行為「在什麼情境或時段發生」或「發生前後的經過」其中一項；非常籠統時算不足。',
  ].join('\n'),
  followup_prompt: '老師的描述資訊不足。請先用一句話同理老師，再依 Skill 的追問清單提出最多 2 個最關鍵、好回答的追問。',
  answer_rules: 'strategies 2–3 個；每個策略的 refs 只能用上面提供的 K 編號，至少一個；不得推測診斷或使用標籤化用語；保留「幼兒A」等代稱。',
  // L5 檢查項目：一行一項「類型＝判斷說明」；類型名稱會成為模型回報 issues.type 的選項，統計也依這個分組
  check_items: [
    '依據＝每個建議是否大致能從它引用的知識推得；合理的實務延伸（例如把原則轉成具體做法）不算編造，只有知識完全沒提到、或與知識矛盾才算問題。',
    '轉介＝{user}描述中有明確需要專人的訊號（安全、疑似受虐、{user}身心危機、長期無效）而回答完全沒提。',
    '語氣＝說教、責怪{user}或家長、或替{subject}下診斷。{user}自己提到的診斷名稱不算違規，只要回答沒有推測或確認診斷即可。',
  ].join('\n'),
  check_severe: '編造或違反知識、遺漏需要專人的訊號、替{subject}下診斷。其他都算小問題。',
  continue_prompt: [
    '老師已經拿到完整建議了，這次不要再重複「看見與理解、策略、觀察、找專人」的完整格式。',
    '- reply：用兩三句話直接回應老師這次說的（新的觀察、試過的結果、或要求），像同事對話一樣自然',
    '- 老師說某個做法有效或無效時，針對這點調整；有新的知識可用時，最多再給 1–2 個「之前沒給過」的做法',
    '- 老師要求特定東西（家長訊息、觀察記錄等）時，只做那一件事，放在對應欄位',
    '- 最後提供下一步選項，讓老師選要往哪個方向走',
  ].join('\n'),
  continue_options: ['幫我寫一段給家長的訊息', '給我接下來的觀察記錄重點', '換一個不同的做法', '這個情況需要找誰協助？'].join('\n'),
  msg_no_knowledge: '謝謝你說出這個狀況。目前知識庫裡沒有足夠、經過檢查的內容可以給你具體建議。建議先和{who}討論，也可以申請特教巡迴輔導老師協助。',
  msg_check_failed: '謝謝你說出這個狀況。我整理出的建議沒有通過依據檢查，為了避免給你不夠可靠的內容，這次先不提供具體做法。建議先和{who}討論，也可以申請特教巡迴輔導老師協助。',
  msg_ai_fallback: '目前知識庫內容不足，以下由 AI 協助補足，尚未經專家審核，請斟酌使用；必要時請和園長或特教巡迴輔導老師確認。',
  msg_out_of_scope: '這個問題超出幼兒園教保現場的範圍，建議洽詢相關專業人員。如果是班上孩子的狀況，歡迎描述你看到的情境。',
};
/** 讀文字設定；設定表沒有這一項時用預設值。{user}{subject}{escalate}{domain} 會換成「角色用詞」設定 */
function text_(key) {
  const v = cfg_(key);
  return tpl_(v == null || String(v).trim() === '' ? (TEXT_DEFAULTS[key] || '') : String(v));
}
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
    ['bot_name', '融合教育小幫手', '老師端顯示的名稱'],
    ['welcome', '你好，我是融合教育小幫手。說說班上遇到的狀況，我會先了解情境，再給你幾個馬上能用的做法。', '老師端的歡迎訊息'],
    ['ui_gate_text', '請輸入園所提供的邀請碼。對話內容會去除孩子姓名後保存，用於改善服務。', '聊天頁：邀請碼頁的說明文字'],
    ['ui_input_placeholder', '描述你在班上遇到的狀況…', '聊天頁：輸入框的提示文字'],
    ['ui_waiting', '正在了解狀況、查找資料…', '聊天頁：送出後等待回答時顯示的字'],
    ['ui_referral_title', '這個情況需要馬上找專人', '聊天頁：緊急轉介卡片的標題'],
    ['ui_referral_note', '是否通報或轉介，由你和園長決定。', '聊天頁：緊急轉介卡片底部的說明'],
    ['ui_referral_heading', '需要時找專人', '聊天頁：回答裡轉介建議的小標題'],
    ['ui_ai_tag', 'AI 補充', '聊天頁：知識不足由 AI 補足的建議旁邊的標籤'],
    ['ui_fb_up', '👍 有幫助', '聊天頁：正面回饋按鈕文字（統計以「有幫助」三個字比對）'],
    ['ui_fb_down', '👎 沒幫助', '聊天頁：負面回饋按鈕文字（統計以「沒幫助」三個字比對）'],
    ['ui_accent', '', '聊天頁：主色（十六進位色碼，例如 #1e7f7d）；留空用預設'],
    ['system_prompt', SYSTEM_PROMPT_DEFAULT, '固定 system prompt（第 1 層）'],
    ['domain_desc', '學前融合教育 AI 系統', '領域描述：分流器與審查器的 prompt 開頭「你是〔領域描述〕的分流器」'],
    ['role_user', '老師', '提問者的稱呼；所有 prompt 裡的 {user} 都換成這個'],
    ['role_subject', '孩子', '談論對象的稱呼（提問者在問誰的事）；{subject}'],
    ['role_escalate', '園長或協同老師', '預設升級對象：轉介資源沒有「系統能力不足」時，保守回答建議找誰；{escalate}'],
    ['intents', TEXT_DEFAULTS.intents, 'L4 意圖選項，一行一個「名稱＝說明」，順序固定：1 新個案（只看這一則）、2 補充同一個案、3 要求特定協助（不追問、延續回覆）、4 與主題無關（拒答）。只改名稱與說明，不要增減行'],
    ['triage_focus_hint', TEXT_DEFAULTS.triage_focus_hint, 'L4 判斷「提問焦點」時給模型看的範例'],
    ['fallback_rules', TEXT_DEFAULTS.fallback_rules, 'knowledge_only 關閉、由 AI 補足時的限制（生成與 L5 檢查都用這段）'],
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
    ['enable_case_flow', true, '個案機制：開＝L4 判斷是否換了新個案、同一個案給 full_answer_rounds 次完整回答後改延續回覆；關＝整段對話都帶進去、每次都完整回答，不切換延續回覆（FAQ 型專案用）'],
    ['enable_triage', true, 'L4 分流：開＝AI 判斷意圖、焦點、轉介、主題、是否追問；關＝不呼叫分流 AI，主題用關鍵字比對，直接檢索知識庫回答（較快較省，但不會偵測緊急轉介、不追問、不判斷超出範圍）'],
    ['enable_llm_check', true, 'L5 AI 檢查：開＝回答後由 AI 依「檢查項目」逐項檢查；關＝只做程式檢查（禁止用語、出處編號）'],
    ['knowledge_only', true, '只用知識庫回答：開＝知識不足時保守回答；關＝知識不足時由 AI 協助補足（照樣檢查，並標示「知識庫內容不足」）'],
    ['enable_relevance_check', true, '是否檢查回答有沒有直接回應老師的提問焦點（L5 關閉時也可單獨啟用）'],
    ['enable_rerank', true, '是否用 AI 依「能不能回答提問焦點」重新排序檢索結果（每題多一次 AI 呼叫）'],
    ['model_eval_gen', '', '自動評測出題用模型；留空＝用檢查模型。建議用和回答不同家的模型，避免自己出題自己答'],
    ['model_eval_judge', '', '自動評測評分用模型；留空＝用檢查模型。建議用和回答不同家的模型，避免自己改自己的考卷'],
    ['enable_node_mapping', false, '上傳文件時就標註（預設關閉：先累積文件，之後在「知識圖譜」分頁一次標註）。開啟時，AI 照 ontology 的標註規格標每一段（主要／次要節點、讀者對象、適用年齡、情境），並提出新節點候選（需先在後台匯入知識圖譜；每份文件多幾次 AI 呼叫）'],
    ['rerank_pool', 12, '重新排序前先取幾段候選'],
    ['quota_min_actionable', 3, '採用的段落中，至少保留幾段「具體做法／案例／溝通範例」'],
    ['quota_max_principle', 2, '採用的段落中，「原則／背景」最多幾段'],
    ['default_skill', '理解孩子', '沒有對應主題時預設載入的 Skill；留空＝不載入主題 Skill，依問題直接回答（「一律載入」的 Skill 仍會載入）'],
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
  make(SHEETS.skills, ['Skill ID', '名稱', '對應主題', '一律載入', '內容', '啟用', '版本', '更新者', '更新時間'],
    SKILLS_DEFAULT.map(s => [s[0], s[1], s[2], s[3], s[4], true, 1, 'setup', new Date()]));
  make(SHEETS.skillHist, ['Skill ID', '版本', '名稱', '對應主題', '一律載入', '內容', '更新者', '更新時間'],
    SKILLS_DEFAULT.map(s => [s[0], 1, s[1], s[2], s[3], s[4], 'setup', new Date()]));
  make(SHEETS.docs, ['文件ID', '標題', '出處', '證據等級', '原始檔名', '段落數', '狀態', '上傳者', '上傳時間', '審核者', '審核時間']);
  make(SHEETS.chunks, ['段落ID', '文件ID', '序號', '內容', '頁碼', '向量', '狀態', '類型', '讀者對象', '適用年齡', '情境']);
  make(SHEETS.synonyms, ['使用者用語', '對應概念'], [['丟', '攻擊行為'], ['推', '攻擊行為'], ['咬', '攻擊行為'], ['暴衝', '攻擊行為'], ['收拾', '轉銜時段'], ['換活動', '轉銜時段'], ['搶', '表達需求'], ['崩潰', '情緒調節'], ['回嘴', '口語對抗'], ['頂嘴', '口語對抗']]);
  make(SHEETS.forbidden, ['詞或規則', '類型', '說明'], [['過動', '詞', '不推測診斷'], ['ADHD', '詞', '不推測診斷'], ['自閉症', '詞', '不推測診斷'], ['亞斯', '詞', '不推測診斷'], ['問題兒童', '詞', '標籤化用語'], ['壞孩子', '詞', '標籤化用語'], ['故意找麻煩', '詞', '標籤化用語'], ['處罰', '詞', '不建議懲罰性做法'], ['懲罰', '詞', '不建議懲罰性做法'], ['吃藥', '詞', '不給醫療建議']]);
  make(SHEETS.referral, ['類別', '該找誰', '聯絡方式', '現在要做的事'], REFERRAL_DEFAULT);
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
