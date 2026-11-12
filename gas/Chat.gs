/**
 * 線上回答流程（P04 對話狀態機）
 * P03 去識別化 → [L4 分流 ‖ P06 檢索向量] → P04 判斷 → P05 載入 Skill → P07 組裝 → A1 生成
 * → [P08 規則 ‖ P09 出處 ‖ L5 AI 檢查] → P10 組合與降級 → P15 紀錄
 */

// ---------- P03 去識別化（enable_deid 關閉時整段略過） ----------
// deid_mode＝ai：用模型找出人名、小名、綽號、機構名，換成代稱（同一個人在整段對話用同一個代稱，對照表只留在使用者端，不寫紀錄）
// deid_mode＝rule：只用規則（幼教雛形的「小＋一字」）。兩種模式都會另外刪手機號碼、Email、身分證字號
const NAME_SKIP = ['小朋', '小孩', '小班', '小組', '小時', '小心', '小事', '小動', '小便', '小肌', '小聲', '小小', '小點', '小任', '小步', '小卡', '小物', '小玩', '小型', '小學', '小姐', '小兒', '小禮', '小貼', '小椅', '小桌', '小籃', '小手', '小部', '小片', '小塊', '小幫'];
const DEID_CATS = ['subject', 'family', 'staff', 'org', 'other'];
// 模型偶爾會把一般名詞當成人名，這些不換
const DEID_GENERIC = ['小朋友', '孩子', '小孩', '幼兒', '老師', '家長', '媽媽', '爸爸', '園長', '主任', '同學', '班上', '園所', '學校', '阿公', '阿嬤', '奶奶', '爺爺', '哥哥', '姊姊', '姐姐', '弟弟', '妹妹'];
/** 代稱前綴：設定 deid_labels，一行一個「類別＝代稱」 */
function deidLabels_() {
  const o = { subject: '幼兒', family: '家長', staff: '老師', org: '某園', other: '某人' };
  text_('deid_labels').split(/\n+/).forEach(l => { const p = l.split(/[＝=]/); if (p.length > 1 && DEID_CATS.indexOf(p[0].trim()) >= 0) o[p[0].trim()] = p.slice(1).join('＝').trim(); });
  return o;
}
function deidScrub_(text) {
  return String(text).replace(/09\d{2}[-\s]?\d{3}[-\s]?\d{3}/g, '（電話已刪除）')
    .replace(/\(0\d\)[-\s]?\d{3,4}[-\s]?\d{4}|0\d[-\s]?\d{3,4}[-\s]?\d{4}/g, '（電話已刪除）')
    .replace(/[\w.+-]+@[\w-]+(\.[\w-]+)+/g, '（Email 已刪除）')
    .replace(/\b[A-Z][12]\d{8}\b/g, '（證號已刪除）');
}
/** 依對照表替換：長的先換，避免「王小明」只換到「小明」 */
function deidApply_(text, map) {
  let out = String(text);
  Object.keys(map).sort((a, b) => b.length - a.length).forEach(n => { out = out.split(n).join(map[n]); });
  return out;
}
function deidNext_(map, prefix) {
  const used = Object.keys(map).filter(k => map[k].indexOf(prefix) === 0).length;
  return prefix + 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[used % 26];
}
function deidRule_(text, map) {
  map = Object.assign({}, map || {});
  const L = deidLabels_();
  const out = deidScrub_(String(text).replace(/小[一-鿿]/g, m => {
    if (NAME_SKIP.indexOf(m) >= 0) return m;
    if (!map[m]) map[m] = deidNext_(map, L.subject);
    return map[m];
  }));
  return { text: deidApply_(out, map), map: map, found: 0 };
}
/** 給模型的「要找哪些個資」說明；單獨呼叫與併入 L4 時共用 */
function deidInstr_(known) {
  const R = roles_();
  return '找出訊息裡所有可以指認特定個人或機構的詞：\n' +
    '- subject：' + R.subject + '的姓名、小名、綽號（例如「小宇」「宇宇」「阿明」「王小明」）\n' +
    '- family：家長或家人的姓名、稱呼（例如「宇宇媽媽」只標「宇宇」，「陳媽媽」標「陳」）\n' +
    '- staff：' + R.user + '或其他人員的姓名\n' +
    '- org：園所、學校、機構、地址或可指認的地點名稱\n' +
    '- other：其他人名\n' +
    '只抓訊息裡「原樣出現」的字串，text 必須能在訊息中逐字找到；一般名詞（小朋友、老師、媽媽、班上）不算。沒有就給空陣列。\n' +
    (known && known.length ? '這些已經處理過，不用再列：' + known.join('、') + '\n' : '');
}
function deidAi_(text, map, C) {
  const r = genJson_(C.model_triage, '你負責在送出前去除個人資訊，只輸出 JSON。' + deidInstr_(Object.keys(map || {})) +
    '格式：{"items":[{"text":"","type":"subject"}]}\n\n訊息：\n' + text, { temperature: 0, noThink: true });
  if (!r || !Array.isArray(r.items)) throw new Error('回覆沒有 items 陣列');
  return deidFromItems_(text, map, r.items);
}
/** 把模型找到的名字清單套成代稱 */
function deidFromItems_(text, map, list) {
  map = Object.assign({}, map || {});
  const L = deidLabels_();
  const items = [].concat(list || []).filter(x => x && typeof x.text === 'string' && x.text.trim().length >= 1);
  items.sort((a, b) => b.text.length - a.text.length).forEach(x => {
    const t = x.text.trim();
    if (map[t] || String(text).indexOf(t) < 0 || t.length > 20 || DEID_GENERIC.indexOf(t) >= 0) return;
    const longer = Object.keys(map).find(k => k.indexOf(t) >= 0 && k !== t);
    if (longer) { map[t] = map[longer]; return; }   // 「小明」是「王小明」的一部分：用同一個代稱
    const cat = DEID_CATS.indexOf(x.type) >= 0 ? x.type : 'other';
    map[t] = deidNext_(map, L[cat]);
  });
  return { text: deidApply_(deidScrub_(text), map), map: map, found: items.length };
}
function deid_(text, map, C) {
  C = C || cfgAll_();
  if (String(C.deid_mode || 'ai') !== 'rule') {
    try { return deidAi_(text, map, C); }
    catch (e) { const d = deidRule_(text, map); d.fallback = e.message; return d; }
  }
  return deidRule_(text, map);
}

// ---------- L4 分流 prompt ----------
function triagePrompt_(prevTeacher, latest, topics, categories, deidKnown) {
  // 規則文字在「設定」的 triage_rules、意圖選項在 intents、稱呼在角色用詞；這裡只放欄位與輸出格式
  // deidKnown 不是 null 時，去識別化併在這次呼叫裡做：多回一個 deid 清單
  const R = roles_(), IN = intents_();
  const withDeid = Array.isArray(deidKnown);
  return '你是' + R.domain + '的分流器，只輸出 JSON。依下列規則判斷：\n' + text_('triage_rules') + '\n\n' +
    '欄位：\n' +
    '- intent：' + R.user + '「這一則訊息」的意圖，從 ' + JSON.stringify(IN.map(i => i.name)) + ' 選一個。' + IN.map(i => i.name + '＝' + i.desc).join('；') + '\n' +
    '- referral.category：從 ' + JSON.stringify(categories) + ' 選一個，或 "none"\n' +
    '- referral.level："緊急"、"需要專人"、"建議諮詢" 或 "none"；referral.reason 一句話\n' +
    '- topics：從 ' + JSON.stringify(topics) + ' 選出相關的（可多選）\n' +
    '- in_scope：是否屬於服務範圍\n' +
    '- info_enough：整個個案的資訊是否足夠\n' +
    '- missing：缺少的關鍵資訊（最多 2 項）\n' +
    '- focus：用一句話寫出' + R.user + '「這一則」真正想要的東西（' + text_('triage_focus_hint') + '），補充或回答追問時寫整個個案目前要解決的事\n' +
    (withDeid ? '- deid：' + deidInstr_(deidKnown) : '') +
    '格式：{' + (withDeid ? '"deid":[{"text":"","type":"subject"}],' : '') + '"intent":"' + IN[0].name + '","focus":"","referral":{"category":"none","level":"none","reason":""},"topics":[],"in_scope":true,"info_enough":true,"missing":[]}\n\n' +
    (prevTeacher.length ? R.user + '先前說過：\n' + prevTeacher.join('\n') + '\n\n' : '') +
    R.user + '這一則訊息：\n' + latest;
}

function runChat_(req, opts) {
  opts = opts || {};
  const t0 = Date.now(), trace = [];
  // kind 讓觀測面板知道怎麼呈現：triage、retrieval、checks、skills、prompt、json、text
  const T = (step, detail, kind) => trace.push({ step: step, kind: kind || (typeof detail === 'string' ? 'text' : 'json'), detail: detail, ms: Date.now() - t0 });
  const metrics = { retrieved: null, top: null, cited: null, skills: [] };
  const stab = stabReset_();
  const quality = { gap: null, l5: [] };   // 知識不足與 L5 發現的問題，寫進對話紀錄「品質問題」欄做統計
  const C = cfgAll_(), R = roles_();
  const maxFu = Number(C.max_followups == null ? 1 : C.max_followups);

  // 1. P03 去識別化（可關閉：原文直接進流程與紀錄）
  //    deid_mode＝ai 且 L4 開著時併進 L4 那次呼叫（省一次呼叫）：先把已知的名字換掉，新名字由 L4 回報後再換
  const useDeid = truthy_(C.enable_deid);
  const useTriage = truthy_(C.enable_triage == null || C.enable_triage === '' ? true : C.enable_triage);
  const mergeDeid = useDeid && useTriage && String(C.deid_mode || 'ai') !== 'rule';
  const raw = String(req.message || '');
  let d = !useDeid ? { text: raw, map: {} }
    : mergeDeid ? { text: deidApply_(deidScrub_(raw), req.nameMap || {}), map: Object.assign({}, req.nameMap || {}) }
    : deid_(raw, req.nameMap, C);
  const history = (req.history || []).slice(-8);
  const prevTeacher = history.filter(h => h.role === 'teacher').map(h => h.text);
  let fullCase = prevTeacher.concat([d.text]).join('\n');
  // 觀測面板與紀錄只顯示代稱，不顯示原名（對照表只回給使用者端）
  const deidNote = () => '代稱：' + (Object.keys(d.map).length ? Object.keys(d.map).map(k => d.map[k]).join('、') : '無');
  const p03 = { step: 'P03 去識別化', kind: 'text', detail: '', ms: Date.now() - t0 };
  if (!useDeid) { p03.step = 'P03 去識別化（已關閉）'; p03.detail = '原文直接使用'; }
  else if (!mergeDeid) p03.detail = (String(C.deid_mode || 'ai') === 'rule' ? '規則' : 'AI') + (d.fallback ? '（AI 失敗改用規則：' + d.fallback + '）' : '') + '｜' + deidNote();
  else p03.detail = '併入 L4 處理中';
  trace.push(p03);

  // 2. L4 分流 ‖ P06 查詢向量（fetchAll 並行）；向量同時算「整段個案」與「只有最新訊息」，分流後再選用
  const skills = skills_();
  const topics = Array.from(new Set([].concat.apply([], skills.filter(s => !s.always).map(s => s.tags))));
  const refTable = referral_();
  const expAll = expandQuery_(fullCase), expNew = expandQuery_(d.text);
  // enable_triage 關閉：不呼叫分流 AI，主題改用關鍵字比對（Skill 對應主題、同義詞概念），直接檢索知識庫回答
  //  代價：不偵測轉介、不判斷超出範圍、不追問、不判斷是否換了新個案（同一段對話都當同一個案）
  let tri, vecs;
  if (useTriage) {
    const triReq = genReq_(C.model_triage, [{ text: triagePrompt_(prevTeacher, d.text, topics, Object.keys(refTable), mergeDeid ? Object.keys(d.map) : null) }], { json: true, temperature: 0, noThink: true });
    const [triRes, embRes] = UrlFetchApp.fetchAll([triReq, embedReqs_([expAll.text, expNew.text], 'RETRIEVAL_QUERY')[0]]);
    stabAt_('L4');
    tri = parseJson_(genText_(genFix_(triReq, triRes)));
    vecs = embedParse_(embRes);
    validateTriage_(tri);
    if (mergeDeid) {
      // L4 回報的名字套成代稱；沒回清單就退回規則
      if (Array.isArray(tri.deid)) { d = deidFromItems_(d.text, d.map, tri.deid); p03.detail = 'AI（併入 L4）｜' + deidNote(); }
      else { stabNote_('缺欄位', 'deid'); const dr = deidRule_(d.text, d.map); d = dr; p03.detail = 'AI（L4 沒回報名字清單，改用規則）｜' + deidNote(); }
      delete tri.deid;
      // 分流結果本身也可能寫到原名（focus、缺的資訊、轉介原因），一併換成代稱後才進紀錄與 prompt
      if (tri.focus) tri.focus = deidApply_(str_(tri.focus), d.map);
      if (Array.isArray(tri.missing)) tri.missing = tri.missing.map(m => deidApply_(str_(m), d.map));
      if (tri.referral && tri.referral.reason) tri.referral.reason = deidApply_(str_(tri.referral.reason), d.map);
      fullCase = prevTeacher.concat([d.text]).join('\n');
      p03.ms = Date.now() - t0;
    }
  } else {
    vecs = embedParse_(UrlFetchApp.fetch.apply(null, reqArgs_(embedReqs_([expAll.text, expNew.text], 'RETRIEVAL_QUERY')[0])));
    const words = fullCase + '\n' + expAll.concepts.join('\n');
    tri = { intent: intentName_(prevTeacher.length ? 'more' : 'new'), focus: '', referral: { category: 'none', level: 'none' },
      topics: topics.filter(t => t && words.indexOf(t) >= 0), in_scope: true, info_enough: true, missing: [], skipped: true };
  }
  const ref = tri.referral && tri.referral.category && tri.referral.category !== 'none' && tri.referral.level && tri.referral.level !== 'none' ? tri.referral : null;
  if (useTriage) T('L4 分流', tri, 'triage');
  else T('L4 分流（已關閉）', '未呼叫分流 AI；主題依關鍵字比對：' + (tri.topics.join('、') || '無') + '｜不偵測轉介、不追問、同一段對話視為同一個案');

  // 意圖：new 就只看這一則，舊的對話不帶進來；其他意圖延續整段個案（名稱可在設定改，流程依 key 判斷）
  // enable_case_flow 關閉：不分個案、不切換延續回覆，整段對話都帶進去、每次都完整回答（FAQ 型專案用）
  const caseFlow = truthy_(C.enable_case_flow == null || C.enable_case_flow === '' ? true : C.enable_case_flow);
  let intentKey = intentKey_(tri.intent) || (prevTeacher.length ? 'more' : 'new');
  if (!caseFlow && intentKey !== 'off') intentKey = prevTeacher.length ? 'more' : 'new';
  const intent = intentName_(intentKey);
  tri.intent_key = intentKey;   // 觀測面板與紀錄用：名稱可改，key 固定
  const isNew = intentKey === 'new' || !prevTeacher.length;
  const caseText = isNew ? d.text : fullCase;
  const ctxHistory = isNew ? [] : history;
  const exp = isNew ? expNew : expAll, qvec = isNew ? vecs[1] : vecs[0];
  const answered = isNew ? 0 : Number(req.answered || 0);     // 這個個案已經給過幾次完整回答
  const fuDone = isNew ? 0 : Number(req.followups || 0);
  T('P06 同義詞擴充', exp.concepts.length ? exp.words.join('、') + ' → ' + exp.concepts.join('、') : '無');

  const base = { nameMap: d.map, deidText: d.text, triage: tri, metrics: metrics, intent: intent, newCase: isNew, answered: answered };
  let evalHits = [];   // 自動評測用：最後採用的段落
  const finish = (out) => {
    const res = Object.assign(base, out, { ms: Date.now() - t0 });
    res.quality = quality;
    res.schema = schema_();   // 前端依這份欄位定義呈現回答（不寫進紀錄）
    res.stability = { models: { triage: useTriage ? C.model_triage : '', answer: C.model_answer, check: C.model_check, rerank: C.model_rerank || '' }, ev: stab.ev, rewriteWhy: stab.rewriteWhy || [] };
    if (!opts.noLog) { try { logTurn_(req, res, trace); } catch (e) { console.warn(e); } }   // 自動評測不寫對話紀錄
    if (opts.trace) res.trace = trace;
    if (opts.withHits) res.hits = evalHits;
    return res;
  };

  // 3. P04 判斷
  if (ref && ref.level === '緊急') {
    T('P04 狀態機', '緊急轉介：' + ref.category + ' → 不走一般回答');
    const r = refTable[ref.category] || {};
    return finish({ type: 'referral', referral: { category: ref.category, level: ref.level, reason: ref.reason, who: r.who, contact: r.contact, now: r.now } });
  }
  if (intentKey === 'off' || tri.in_scope === false) {
    T('P04 狀態機', '超出服務範圍（' + intent + '）');
    return finish({ type: 'out_of_scope', message: text_('msg_out_of_scope') });
  }
  const pick = skills.filter(s => s.always || s.tags.some(t => (tri.topics || []).indexOf(t) >= 0));
  if (!pick.some(s => !s.always)) { const def = skills.find(s => s.name === C.default_skill); if (def) pick.push(def); }
  const sysPrompt = String(C.system_prompt || '');
  const noTopic = !pick.some(s => !s.always);   // 沒有對應的主題 Skill，且 default_skill 留空
  const skillText = pick.map(s => '【' + s.name + '】\n' + s.content).join('\n\n') +
    (noTopic ? (pick.length ? '\n\n' : '') + '（這題沒有對應的主題 Skill：依' + R.user + '的問題本身與檢索到的知識直接回答，不必套用特定框架。）' : '');
  metrics.skills = pick.map(s => s.name);

  if (intentKey !== 'specific' && !tri.info_enough && fuDone < maxFu) {
    T('P04 狀態機', '資訊不足 → 追問');
    T('P05 Skill 載入', pick.map(s => ({ name: s.name, version: s.version, always: s.always })), 'skills');
    const fu = genJson_(C.model_answer, sysPrompt + '\n\n' + skillText + '\n\n' + text_('followup_prompt') + '\n目前缺少：' + ((tri.missing || []).join('、') || '情境細節') +
      '\n只輸出 JSON：{"empathy":"","questions":["",""]}\n\n' + R.user + '的描述：\n' + caseText, { temperature: 0.4, noThink: true });
    T('A1 追問', fu);
    return finish({ type: 'followup', empathy: str_(fu.empathy), questions: (fu.questions || []).map(str_).slice(0, 2), followups: fuDone + 1 });
  }

  // 前幾輪給完整回答；之後（或使用者點選特定協助）改成「延續回覆」：只回應新內容，並提供下一步選項
  const fullRounds = Number(C.full_answer_rounds == null || C.full_answer_rounds === '' ? 2 : C.full_answer_rounds);
  const mode = !caseFlow ? 'full' : intentKey === 'specific' && !isNew ? 'continue' : (answered >= fullRounds ? 'continue' : 'full');
  T('P04 狀態機', intent + '｜' + (tri.info_enough ? '資訊足夠' : '已追問過') + '｜已完整回答 ' + answered + ' 次 → ' +
    (mode === 'full' ? '完整回答' : '延續回覆') + (caseFlow ? '' : '（個案機制已關閉）') + (ref ? '（附轉介：' + ref.category + '・' + ref.level + '）' : ''));
  T('P05 Skill 載入', pick.map(s => ({ name: s.name, version: s.version, always: s.always })), 'skills');

  // 4. P06 檢索
  const focus = str_(tri.focus) || d.text;
  const poolSize = Math.max(Number(C.rerank_pool || 12), Number(C.top_k || 6));
  // 知識圖譜（可關閉）：依問題找節點 → 段落加分或只用這些段落；節點定義放進回答 prompt 當背景
  let kg = null;
  if (truthy_(C.enable_kg)) {
    kg = kgMatch_(caseText + '\n' + focus, exp.concepts, C);
    const kgMode = String(C.kg_mode || 'hybrid');
    T('知識圖譜', { mode: kgMode, nodes: kg.nodes.map(n => (n.hop ? '↳ ' : '') + n.name + '（' + n.type + '）'), chunks: Object.keys(kg.chunks).length, note: kg.note }, 'json');
    if (kgMode === 'only' && !Object.keys(kg.chunks).length) T('P06 檢索', '圖譜 only 模式但沒有對到任何段落 → 視為沒有可用知識');
  }
  const sd = searchDetail_(qvec, caseText, exp.concepts, null, poolSize, kg);
  metrics.top = sd.cands.length ? sd.cands[0].score : 0;
  T('P06 檢索', { minScore: sd.minScore, topK: sd.topK, total: sd.total, cands: sd.cands, kgMode: sd.kgMode }, 'retrieval');
  const rr = rerankSelect_(focus, caseText, sd.hits, C);
  const hits = rr.hits;
  evalHits = hits.map(h => ({ id: h.id, docId: h.docId, ref: h.ref, title: h.title, text: h.text }));
  sd.cands.forEach(c => { c.ref = (hits.find(h => h.id === c.id) || {}).ref || ''; });   // 檢索明細改用最後的 K 編號
  metrics.retrieved = hits.length; metrics.actionable = rr.actionable; metrics.direct = rr.direct;
  if (sd.hits.length) T('重新排序與配額', rr.trace, 'rerank');
  // 知識不足：knowledge_only 開＝保守回答；關＝由 AI 協助補足（照樣過全部檢查，並標示）
  let fallback = false;
  const kOnly = truthy_(C.knowledge_only == null || C.knowledge_only === '' ? true : C.knowledge_only);
  if (mode === 'full' && (!hits.length || !rr.direct)) {
    const why = hits.length ? '沒有能直接回答焦點的段落（只有背景或原則）' : '沒有可用知識';
    quality.gap = { kind: hits.length ? '只有背景或原則' : '沒有可用知識', outcome: kOnly ? '保守回答' : 'AI 補足', focus: focus, topics: tri.topics || [], top: metrics.top };
    if (kOnly) {
      T('P10 降級', why + ' → 保守回答，記為知識缺口');
      return finish({ type: 'conservative', message: conservativeMsg_(refTable), sources: [] });
    }
    fallback = true;
    T('P04 狀態機', why + ' → 由 AI 協助補足（knowledge_only 已關閉），記為知識缺口');
  }

  // 5. P07 組裝 + A1 生成，最多重寫一次
  const refWho = ref ? ((refTable[ref.category] || {}).who || '') : '';
  const range = itemsRange_(mode);
  let ans, chk, rewritten = false, maxAttempts = 2, fbNote = null;
  // 生成或檢查時才發現知識不足（檢索分數高但談的是別的對象）：不要逼模型硬寫，改走知識不足的處理
  const gapFound = (kind, detail, attempt) => {
    quality.gap = { kind: kind, outcome: kOnly ? '保守回答' : 'AI 補足', focus: focus, topics: tri.topics || [], top: metrics.top, detail: String(detail || '').slice(0, 160) };
    if (kOnly) { T('P10 降級', kind + '（' + detail + '）→ 保守回答，記為知識缺口'); return 'stop'; }
    T('P04 狀態機', kind + '（' + detail + '）→ 改由 AI 協助補足並標示，記為知識缺口');
    fallback = true; rewritten = true; maxAttempts = Math.max(maxAttempts, attempt + 2); fbNote = null;
    return 'retry';
  };
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const fb = fbNote; fbNote = null;
    const prompt = mode === 'full'
      ? assemble_(sysPrompt, skillText, tri, caseText, hits, ctxHistory, ref, refWho, fb, focus, fallback, kg)
      : assembleContinue_(sysPrompt, skillText, tri, prevTeacher, d.text, hits, ctxHistory, ref, refWho, intent, fb, focus, fallback, kg);
    if (attempt === 0) T('P07 Prompt 組裝', { summary: (mode === 'full' ? '完整回答' : '延續回覆') + '｜固定 system prompt ' + sysPrompt.length + ' 字｜Skill ' + skillText.length + ' 字｜知識 ' + hits.length + ' 段｜歷史 ' + ctxHistory.length + ' 則｜共 ' + prompt.length + ' 字', prompt: prompt }, 'prompt');
    stabAt_('A1');
    let raw;
    try { raw = genJson_(C.model_answer, prompt, { temperature: Number(C.temperature || 0.3) }); }
    catch (e) {
      // 回覆讀不出 JSON：當成這一版沒通過，重寫一次（原本會直接出錯）
      if (!/JSON/.test(e.message)) throw e;
      chk = { pass: false, items: [{ name: '格式', pass: false, note: '回覆不是有效的 JSON' }], reasons: ['上一版回覆不是有效的 JSON，請只輸出 JSON'] };
      T(attempt ? 'A1 重寫' : 'A1 生成', '回覆不是有效的 JSON'); (stab.rewriteWhy = stab.rewriteWhy || []).push('格式');
      fbNote = chk.reasons; rewritten = true; continue;
    }
    validateAnswer_(raw, mode);
    const kg = str_(raw.knowledge_gap).trim();
    ans = cleanAnswer_(raw, mode);
    T(attempt ? 'A1 重寫' : 'A1 生成', '建議 ' + answerItems_(ans).length + ' 個' + (kg && kg !== 'null' ? '｜模型回報知識不足：' + kg : ''));
    if (kg && kg !== 'null' && !fallback) {
      chk = { pass: false, items: [], reasons: [] };
      if (gapFound('回答時發現知識不足', kg, attempt) === 'stop') return finish({ type: 'conservative', message: conservativeMsg_(refTable), sources: [] });
      continue;
    }
    stabAt_('L5');
    chk = checks_(caseText, ans, hits, C, range, focus, fallback);
    T('檢查', chk.items, 'checks');
    if ((chk.l5 && chk.l5.length) || chk.relFail) quality.l5.push({ attempt: attempt, issues: chk.l5 || [], relevance: chk.relFail || null });
    if (!chk.pass && chk.relFail && chk.relFail.cause === '知識不足' && !fallback) {
      if (gapFound('檢查發現知識不足', chk.relFail.note || '知識沒有針對這個問題', attempt) === 'stop') return finish({ type: 'conservative', message: conservativeMsg_(refTable), sources: [] });
      continue;
    }
    if (chk.pass) break;
    fbNote = chk.reasons;
    (stab.rewriteWhy = stab.rewriteWhy || []).push.apply(stab.rewriteWhy, chk.items.filter(x => !x.pass).map(x => x.name));
    rewritten = true;
  }
  if (!chk.pass) {
    quality.l5Final = '保守回答';
    T('P10 降級', '重寫後仍未通過 → 保守回答：' + chk.reasons.join('；'));
    return finish({ type: 'conservative', message: conservativeMsg_(refTable, 'check'), sources: [], checks: chk.items });
  }
  T('P10 組合', '檢查全部通過' + (rewritten ? '（重寫一次後）' : ''));
  const used = {}; answerItems_(ans).forEach(s => (s.refs || []).forEach(r => used[r] = true));
  metrics.cited = Object.keys(used).filter(r => hits.some(h => h.ref === r)).length;
  metrics.usedRefs = Object.keys(used);
  const sources = hits.filter(h => used[h.ref]).map(h => ({ ref: h.ref, title: h.title, source: h.source, page: h.page, evidence: h.evidence, snippet: h.text.slice(0, 280) }));
  const referral = ref ? { category: ref.category, level: ref.level, who: refWho, contact: (refTable[ref.category] || {}).contact || '' } : null;
  if (mode === 'continue') {
    ans.options = (ans.options && ans.options.length ? ans.options : continueOptions_()).map(str_).filter(Boolean).slice(0, 4);
    if (fallback) ans.notice = text_('msg_ai_fallback');
    return finish({ type: 'continue', answer: ans, sources: sources, referral: referral, checks: chk.items, rewritten: rewritten, answered: answered + 1 });
  }
  if (fallback) ans.notice = text_('msg_ai_fallback');
  return finish({ type: fallback ? 'ai_fallback' : 'answer', answer: ans, sources: sources, referral: referral, checks: chk.items, rewritten: rewritten, answered: answered + 1 });
}

// ---------- 穩定度：檢查模型回的 JSON 有沒有照格式（只記錄，不改變流程） ----------
function validateTriage_(t) {
  if (!t || typeof t !== 'object') return stabNote_('型別錯誤', '分流結果不是物件');
  ['intent', 'focus', 'referral', 'topics', 'in_scope', 'info_enough'].forEach(k => { if (!(k in t)) stabNote_('缺欄位', k); });
  if ('intent' in t && !intentKey_(t.intent)) stabNote_('選項不符', 'intent＝' + t.intent);
  if ('topics' in t && !Array.isArray(t.topics)) stabNote_('型別錯誤', 'topics 不是陣列');
}
function validateAnswer_(a, mode) {
  if (!a || typeof a !== 'object' || Array.isArray(a)) return stabNote_('型別錯誤', '回答不是物件');
  schemaFields_(mode).forEach(f => {
    if (!(f.key in a)) { if (f.required) stabNote_('缺欄位', f.key); return; }
    const v = a[f.key];
    if (f.type === 'text') { if (v != null && typeof v !== 'string') stabNote_('型別錯誤', f.key + ' 不是文字'); }
    else if (v != null && !Array.isArray(v)) stabNote_('型別錯誤', f.key + ' 不是陣列');
    if (f.type === 'items') [].concat(Array.isArray(v) ? v : []).forEach((s, i) => {
      if (!s || typeof s !== 'object') return stabNote_('型別錯誤', f.key + (i + 1) + ' 不是物件');
      ['title', 'how', 'refs'].forEach(k => { if (!(k in s)) stabNote_('缺欄位', f.key + (i + 1) + '.' + k); });
      if ('refs' in s && !Array.isArray(s.refs)) stabNote_('型別錯誤', f.key + (i + 1) + '.refs 不是陣列');
    });
  });
  if (!('referral' in a)) stabNote_('缺欄位', 'referral');
}

// ---------- 回答欄位定義（設定 answer_schema）：程式只認 type，欄位名稱與標題都由設定決定 ----------
const SCHEMA_TYPES = ['text', 'list', 'items'];
function citationOn_() { const v = cfgAll_().enable_citation; return truthy_(v == null || v === '' ? true : v); }
let SCHEMA_ = null;
function schema_() {
  if (SCHEMA_) return SCHEMA_;
  let out = null, bad = null;
  try { out = parseSchema_(text_('answer_schema')); } catch (e) { bad = e.message; }
  // 出處機制開著時至少要有一個 items 欄位（P09 要檢查 refs）；關閉時允許只有文字欄位
  if (out && citationOn_() && !out.some(f => f.type === 'items')) { bad = '缺少 items 欄位（enable_citation 開啟時必須有）'; out = null; }
  if (!out) { out = parseSchema_(TEXT_DEFAULTS.answer_schema); stabNote_('設定錯誤', 'answer_schema：' + bad + '，改用預設欄位'); }
  return (SCHEMA_ = out);
}
function parseSchema_(text) {
  const raw = JSON.parse(text);
  if (!Array.isArray(raw)) throw new Error('不是陣列');
  const seen = {}, reserved = ['refs', 'knowledge_gap', 'referral', 'options', 'notice'];
  return raw.map(f => ({
    key: String(f.key || '').trim(), type: SCHEMA_TYPES.indexOf(f.type) >= 0 ? f.type : 'text',
    label: str_(f.label), labelContinue: f.label_continue == null ? str_(f.label) : str_(f.label_continue), hint: tpl_(str_(f.hint)),
    modes: f.modes === 'continue' || f.modes === 'both' ? f.modes : 'full', required: !!f.required, summary: !!f.summary, copy: !!f.copy,
    count: rangeOf_(f.count, [2, 3]), countContinue: rangeOf_(f.count_continue, [0, 2]),
  })).filter(f => f.key && /^[A-Za-z_][A-Za-z0-9_]*$/.test(f.key) && reserved.indexOf(f.key) < 0 && !seen[f.key] && (seen[f.key] = true));
}
function rangeOf_(s, def) { const m = /^\s*(\d+)\s*[-–~]\s*(\d+)\s*$/.exec(String(s == null ? '' : s)); return m ? [Number(m[1]), Number(m[2])] : def; }
function schemaFields_(mode) { return schema_().filter(f => f.modes === 'both' || f.modes === mode); }
function itemsRange_(mode) { const f = schemaFields_(mode).find(x => x.type === 'items'); return f ? (mode === 'full' ? f.count : f.countContinue) : [0, 3]; }
/** 紀錄與評測用的一行摘要：summary 欄位的文字＋建議標題 */
function answerSummary_(a, type) {
  const mode = type === 'continue' ? 'continue' : 'full';
  return schemaFields_(mode).filter(f => f.summary).map(f => { const v = (a || {})[f.key]; if (v == null || (Array.isArray(v) && !v.length)) return ''; return f.type === 'items' ? v.map(s => s.title).join('、') : Array.isArray(v) ? v.join('；') : String(v); }).filter(Boolean).join('｜');
}
/** 所有 items 欄位的內容合併（P09 出處、引用統計、評測都用這個） */
function answerItems_(a) { return [].concat.apply([], schema_().filter(f => f.type === 'items').map(f => [].concat((a || {})[f.key] || []))); }
/** 給模型看的輸出格式：依欄位定義拼 JSON 範例，系統欄位固定接在後面 */
function schemaFormat_(mode, extra) {
  const parts = schemaFields_(mode).map(f => {
    const h = f.hint || f.label || f.key;
    if (f.type === 'items') return '"' + f.key + '":[{"title":"標題","how":"' + h + '","refs":' + (citationOn_() ? '["K1"]' : '[]') + '}]';
    if (f.type === 'list') return '"' + f.key + '":["' + h + '"]';
    return '"' + f.key + '":"' + h + '"';
  });
  return '{' + parts.concat(extra || []).join(',') + '}';
}

// ---------- 回答清理：欄位一律轉成文字，去掉模型自己寫進內文的 (K1) ----------
function str_(v) {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  if (Array.isArray(v)) return v.map(str_).filter(Boolean).join('；');
  if (typeof v === 'object') return Object.keys(v).map(k => str_(v[k])).filter(Boolean).join('；');
  return String(v);
}
function stripRefs_(t) {
  return str_(t).replace(/\s*[（(\[【]\s*K\d+(\s*[,，、]\s*K\d+)*\s*[)）\]】]/g, '').replace(/\s*K\d+(\s*[,，、]\s*K\d+)*\s*$/g, '').trim();
}
function cleanAnswer_(a, mode) {
  a = a || {};
  const out = {};
  // 只保留欄位定義裡這個模式的欄位＋系統欄位；型別容錯：refs 給成字串、list 不是陣列時也不會出錯
  schemaFields_(mode || 'full').forEach(f => {
    const v = a[f.key];
    if (f.type === 'items') out[f.key] = [].concat(v || []).filter(s => s && typeof s === 'object').map(s => ({ title: stripRefs_(s.title), how: stripRefs_(s.how), refs: [].concat(s.refs || []).map(String).filter(Boolean) }));
    else if (f.type === 'list') out[f.key] = [].concat(v == null || v === 'null' ? [] : v).map(stripRefs_).filter(Boolean);
    else out[f.key] = v == null || v === 'null' ? null : (stripRefs_(v) || null);
  });
  out.referral = a.referral == null || a.referral === 'null' ? null : (stripRefs_(a.referral) || null);
  if (a.options) out.options = [].concat(a.options).map(str_).filter(Boolean);
  return out;
}
function rangeText_(mode) { const r = itemsRange_(mode); return r[0] + '–' + r[1]; }
function continueOptions_() {
  return text_('continue_options').split(/\n+/).map(s => s.replace(/^[-・\d.、\s]+/, '').trim()).filter(Boolean);
}

/** 延續回覆：已經給過完整建議，這次只回應新的內容或使用者要的那件事 */
function assembleContinue_(sys, skillText, tri, prevTeacher, latest, hits, history, ref, refWho, intent, feedback, focus, fallback, kg) {
  const R = roles_();
  const forbiddenWords = forbidden_().filter(f => !f.regex).map(f => f.rule);
  const hist = history.map(h => (h.role === 'teacher' ? R.user + '：' : '助手：') + h.text).join('\n');
  const kn = hits.map(h => h.ref + '｜' + h.title + (h.page ? '（第' + h.page + '頁）' : '') + '\n' + h.text).join('\n\n');
  return [
    '【1 固定 system prompt】\n' + sys,
    '【2 主題 Skill】\n' + skillText,
    kgPromptText_(kg) ? '【知識圖譜背景】（與問題相關的概念與做法方向，只能當理解背景，不能當出處）\n' + kgPromptText_(kg) : '',
    '【3 對話到目前為止】\n' + (hist || '（無）'),
    '【4 ' + R.user + '這次說的】（' + intent + '）\n' + latest + (focus ? '\n' + R.user + '想要的：' + focus : ''),
    fallback
      ? '【5 知識庫內容不足】' + (kn ? '以下知識沒有直接針對這次的問題，只能當背景引用（編號 K1…）：\n' + kn + '\n' : '') +
        '這次可以用一般實務知識補足：' + text_('fallback_rules')
      : kn ? '【5 檢索到的知識（只能引用這些，編號 K1…）】\n' + kn : '【5 檢索到的知識】這次沒有找到新的相關知識，不要給新的策略。',
    '【延續回覆的做法】\n' + text_('continue_prompt') + '\n可提供的下一步選項（挑 2–4 個最適合的，可改寫成符合情境的說法）：\n' + continueOptions_().map(o => '- ' + o).join('\n'),
    feedback ? '【重寫指示】上一版沒有通過檢查：' + feedback.join('；') + '。請修正後重寫。' : '',
    '輸出格式（只輸出 JSON，沒有內容的欄位填 null 或空陣列）：' + schemaFormat_('continue', ['"referral":' +
      (ref ? '"依「' + ref.level + '」簡短說明該找誰（可找：' + refWho + '）"' : 'null'), '"options":["下一步選項"]', '"knowledge_gap":null']) + '\n' +
      (fallback ? '' : '若這次的問題換了對象或主題，而上面的知識沒有在談它，不要硬套：knowledge_gap 填一句話說明缺少什麼；知識有涵蓋時為 null。\n') +
      '規則：有出處的建議 ' + rangeText_('continue') + ' 個，只放之前沒給過的新做法，' + (fallback ? '有引用知識才填 refs；' : '每個都要有 refs；') + '不要重複之前回答過的內容；不得推測診斷；保留去識別化的代稱；refs 編號不要寫進文字裡。' +
      (forbiddenWords.length ? '不得出現這些詞：' + forbiddenWords.join('、') + '。' : ''),
  ].filter(Boolean).join('\n\n');
}

function assemble_(sys, skillText, tri, caseText, hits, history, ref, refWho, feedback, focus, fallback, kg) {
  const R = roles_();
  const forbiddenWords = forbidden_().filter(f => !f.regex).map(f => f.rule);
  const hist = history.map(h => (h.role === 'teacher' ? R.user + '：' : '助手：') + h.text).join('\n');
  const kn = hits.map(h => h.ref + '｜' + h.title + (h.page ? '（第' + h.page + '頁）' : '') + '\n' + h.text).join('\n\n');
  return [
    '【1 固定 system prompt】\n' + sys,
    '【2 主題 Skill】\n' + skillText,
    kgPromptText_(kg) ? '【知識圖譜背景】（與問題相關的概念與做法方向，只能當理解背景，不能當出處）\n' + kgPromptText_(kg) : '',
    '【3 情境資訊】\n分流結果：' + JSON.stringify(tri) + '\n' + R.user + '原話：\n' + caseText +
      (focus ? '\n\n【' + R.user + '這次想要的】' + focus + '\n回答要先直接回應這一點；策略要具體到這個' + R.subject + '與情境（做什麼、什麼時候、怎麼說），不要只寫一般原則。' : ''),
    fallback
      ? '【4 知識庫內容不足】' + (kn ? '以下只是相關的背景或原則，可以引用（編號 K1…），但沒有直接針對這個情況的做法：\n' + kn : '知識庫沒有相關內容。') +
        '\n這次可以用一般實務知識補足：' + text_('fallback_rules')
      : '【4 檢索到的知識（只能引用這些，編號 K1…）】\n' + kn,
    hist ? '【5 對話歷史】\n' + hist : '',
    feedback ? '【重寫指示】上一版沒有通過檢查：' + feedback.join('；') + '。請修正後重寫。' : '',
    '輸出格式（只輸出 JSON）：' + schemaFormat_('full', ['"knowledge_gap":null', '"referral":' +
      (ref ? '"依「' + ref.level + '」說明為什麼需要、該找誰（可找：' + refWho + '）"' : 'null')]) + '\n' +
      (fallback ? '' : '若上面的知識沒有在談這個問題的對象或情況，不要硬套：knowledge_gap 填一句話說明缺少什麼，有出處的建議給空陣列；知識有涵蓋時 knowledge_gap 為 null。\n') +
      '規則：' + (fallback ? '有出處的建議 ' + rangeText_('full') + ' 個；引用背景知識時 refs 只能用上面提供的 K 編號，來自一般知識時 refs 為空陣列；不得推測診斷或使用標籤化用語；保留去識別化的代稱。' : text_('answer_rules')) +
      (forbiddenWords.length ? '回答中不得出現這些詞（即使是否定句也不要寫）：' + forbiddenWords.join('、') + '。' : ''),
  ].filter(Boolean).join('\n\n');
}

// ---------- 檢查：P08 規則、P09 出處、L5 AI 檢查（項目在設定 check_items） ----------
function checks_(caseText, ans, hits, C, range, focus, fallback) {
  range = range || [2, 3];
  const R = roles_();
  const items = [], reasons = []; let l5Issues = [], relFail = null;
  const s = JSON.stringify(ans);
  const bad = forbidden_().find(f => f.regex ? new RegExp(f.rule).test(s) : s.indexOf(f.rule) >= 0);
  items.push({ name: 'P08 規則檢查', pass: !bad, note: bad ? '出現禁止用語「' + bad.rule + '」' : '沒有禁止用語' });
  if (bad) reasons.push('不可使用「' + bad.rule + '」' + (bad.note ? '（' + bad.note + '）' : ''));
  const ok = {}; hits.forEach(h => ok[h.ref] = true);
  const st = answerItems_(ans); const p9 = [];
  if (citationOn_()) {
    // P09：enable_citation 關閉時不檢查數量與出處（回答可以只有文字欄位）
    if (st.length < range[0] || st.length > range[1]) p9.push('建議數量 ' + st.length + ' 個，應為 ' + range[0] + '–' + range[1] + ' 個');
    st.forEach(x => { if (!fallback && !(x.refs || []).length) p9.push('「' + x.title + '」沒有出處'); (x.refs || []).forEach(r => { if (!ok[r]) p9.push(r + ' 不在檢索結果中'); }); });
    items.push({ name: 'P09 出處核對', pass: !p9.length, note: p9.join('；') || (fallback ? '知識不足，部分建議來自 AI 一般知識（已標示）' : st.length ? '每條建議都有出處' : '沒有新建議') });
    reasons.push.apply(reasons, p9);
  } else items.push({ name: 'P09 出處核對', pass: true, note: '已關閉（enable_citation）' });
  // L5 AI 檢查（可關閉）＋回應度（可另外關閉）；兩者合併為一次 AI 呼叫
  const doL5 = truthy_(C.enable_llm_check);
  const doRel = !!focus && truthy_(C.enable_relevance_check == null || C.enable_relevance_check === '' ? true : C.enable_relevance_check);
  if (doL5 || doRel) {
    try {
      const kn = hits.map(h => h.ref + '：' + h.text).join('\n');
      const CI = doL5 ? checkItems_() : [];
      const l5 = genJson_(C.model_check, '你是' + R.domain + '回答的審查器，只輸出 JSON。\n' +
        (doL5 ? '逐項檢查回答：\n' + CI.map((it, i) => '(' + (i + 1) + ') ' + it.name + '：' + it.desc).join('\n') +
          '\n嚴重（會擋下回答）只用在：' + text_('check_severe') + '\n每個問題的 severe：符合「嚴重」的定義為 true，其他為 false。\n' : '') +
        (doL5 && fallback ? '這次知識庫內容不足，refs 為空的策略來自一般實務知識：「依據」改為檢查是否符合下列補足規則：' + text_('fallback_rules') + '\n越界或可能造成傷害才算嚴重。\n' : '') +
        (doRel ? '另外判斷回應度：回答是否直接回應「' + R.user + '想要的」，策略是否具體到這個' + R.subject + '與情境（不是只有一般原則）。addressed 為 true／false，note 說明缺了什麼；' +
          'addressed 為 false 時，cause 填「知識不足」（提供的知識沒有在談這個問題的對象或情況，重寫也無法改善）或「偏題」（知識有涵蓋，但回答沒用好）。\n' : '') +
        '格式：{' + (doL5 ? '"issues":[{"type":"' + CI.map(it => it.name).join('|') + '","severe":false,"note":"一句話說明問題"}]' : '') + (doL5 && doRel ? ',' : '') +
        (doRel ? '"relevance":{"addressed":true,"cause":null,"note":""}' : '') + '}' + (doL5 ? '，沒有問題時 issues 為空陣列' : '') + '\n\n' +
        (doRel ? R.user + '想要的：' + focus + '\n\n' : '') + R.user + '描述：\n' + caseText + '\n\n知識：\n' + kn + '\n\n回答：\n' + s, { temperature: 0, noThink: true });
      if (doL5 && !Array.isArray(l5.issues)) stabNote_(l5.issues == null ? '缺欄位' : '型別錯誤', 'issues');
      if (doL5 && Array.isArray(l5.issues) && l5.issues.some(x => typeof x === 'string')) stabNote_('型別錯誤', 'issues 項目是文字不是物件');
      if (doRel && (!l5.relevance || typeof l5.relevance.addressed !== 'boolean')) stabNote_(l5.relevance ? '型別錯誤' : '缺欄位', 'relevance.addressed');
      if (doL5) {
        // 模型可能回字串或物件，一律轉成「類型：說明」文字
        const issues = (l5.issues || []).map(x => typeof x === 'string' ? { type: '', severe: true, note: x }
          : { type: x.type || '', severe: x.severe !== false, note: x.note || x.issue || x.reason || x.description || JSON.stringify(x) });
        l5Issues = issues.map(i => ({ type: i.type || '其他', severe: !!i.severe, note: String(i.note).slice(0, 160) }));
        const severe = issues.filter(i => i.severe);
        const txt = i => (i.type ? i.type + '：' : '') + i.note;
        const pass = !severe.length;
        items.push({ name: 'L5 檢查', pass: pass,
          note: !issues.length ? CI.map(it => it.name).join('、') + '都沒有問題' : (pass ? '小問題（不擋）：' : '') + issues.map(i => (i.severe ? '【嚴重】' : '') + txt(i)).join('；') });
        if (!pass) reasons.push.apply(reasons, severe.length ? severe.map(txt) : ['檢查有問題']);
      }
      if (doRel) {
        const rel = l5.relevance || {};
        const ok2 = rel.addressed !== false;
        if (!ok2) relFail = { focus: focus, note: str_(rel.note).slice(0, 160), cause: rel.cause === '知識不足' ? '知識不足' : '偏題' };
        items.push({ name: '回應度', pass: ok2, note: ok2 ? '有直接回應：' + focus : '【嚴重】沒有回應到「' + focus + '」' + (rel.note ? '：' + str_(rel.note) : '') });
        if (!ok2) reasons.push('沒有直接回應' + R.user + '想要的「' + focus + '」' + (rel.note ? '（' + str_(rel.note) + '）' : '') + '，請先直接回應這一點，策略要具體');
      }
    } catch (e) { items.push({ name: doL5 ? 'L5 檢查' : '回應度', pass: true, note: '檢查未完成（略過）：' + e.message }); }
  }
  return { pass: items.every(i => i.pass), items: items, reasons: reasons, l5: l5Issues, relFail: relFail };
}

function conservativeMsg_(refTable, why) {
  const r = refTable['系統能力不足'] || {};
  return text_(why === 'check' ? 'msg_check_failed' : 'msg_no_knowledge').split('{who}').join(r.who || roles_().escalate);
}

// ---------- P15 紀錄 ----------
function logTurn_(req, res, trace) {
  const m = res.metrics || {};
  // 紀錄不存完整 prompt（太長），段落只留前 300 字
  const tr = JSON.stringify(trace, (k, v) => k === 'prompt' ? undefined : v);
  const turn = req.turn || Math.floor((req.history || []).length / 2) + 1;
  append_('logs', [[new Date(), req.session || '', req.school || '', turn, res.deidText || '',
    JSON.stringify(res.triage || {}), res.referral ? res.referral.category + '・' + res.referral.level : '',
    res.type, JSON.stringify(res.answer || res.questions || res.message || ''), (res.sources || []).map(s => s.ref + ':' + s.title).join('｜'),
    JSON.stringify(res.checks || []), res.rewritten ? '是' : '否', res.ms || '',
    m.retrieved == null ? '' : m.retrieved, m.top == null ? '' : m.top, m.cited == null ? '' : m.cited, (m.skills || []).join('、'),
    tr.length > 48000 ? JSON.stringify(trace.map(t => ({ step: t.step, kind: 'text', detail: '（內容過長未保存）', ms: t.ms }))) : tr,
    JSON.stringify(res.stability || {}), JSON.stringify(res.quality || {})]]);
}
