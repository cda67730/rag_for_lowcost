/**
 * 線上回答流程（P04 對話狀態機）
 * P03 去識別化 → [L4 分流 ‖ P06 檢索向量] → P04 判斷 → P05 載入 Skill → P07 組裝 → A1 生成
 * → [P08 規則 ‖ P09 出處 ‖ L5 依據與轉介] → P10 組合與降級 → P15 紀錄
 */

// ---------- P03 去識別化（程式，不用 LLM） ----------
const NAME_SKIP = ['小朋', '小孩', '小班', '小組', '小時', '小心', '小事', '小動', '小便', '小肌', '小聲', '小小', '小點', '小任', '小步', '小卡', '小物', '小玩', '小型', '小學', '小姐', '小兒', '小禮', '小貼', '小椅', '小桌', '小籃', '小手', '小部', '小片', '小塊', '小幫'];
function deid_(text, map) {
  map = Object.assign({}, map || {});
  const labels = 'ABCDEFGHIJ';
  const out = String(text).replace(/小[一-鿿]/g, m => {
    if (NAME_SKIP.indexOf(m) >= 0) return m;
    if (!map[m]) map[m] = '幼兒' + labels[Object.keys(map).length % labels.length];
    return map[m];
  }).replace(/09\d{2}[-\s]?\d{3}[-\s]?\d{3}/g, '（電話已刪除）');
  return { text: out, map: map };
}

// ---------- L4 分流 prompt ----------
const INTENTS = ['新個案', '補充同一個案', '要求特定協助', '與幼教無關'];
function triagePrompt_(prevTeacher, latest, topics, categories) {
  // 規則文字在「設定」的 triage_rules，這裡只放選項清單與輸出格式
  return '你是學前融合教育 AI 系統的分流器，只輸出 JSON。依下列規則判斷：\n' + text_('triage_rules') + '\n\n' +
    '欄位：\n' +
    '- intent：老師「這一則訊息」的意圖，從 ' + JSON.stringify(INTENTS) + ' 選一個。新個案＝換了一個孩子或完全不同的狀況；補充同一個案＝回答追問或補充細節、說試過的結果；要求特定協助＝要家長訊息、觀察記錄、其他策略、找專人等具體東西；與幼教無關＝閒聊、算數、與帶班無關\n' +
    '- referral.category：從 ' + JSON.stringify(categories) + ' 選一個，或 "none"\n' +
    '- referral.level："緊急"、"需要專人"、"建議諮詢" 或 "none"；referral.reason 一句話\n' +
    '- topics：從 ' + JSON.stringify(topics) + ' 選出相關的（可多選）\n' +
    '- in_scope：是否屬於服務範圍\n' +
    '- info_enough：整個個案的資訊是否足夠\n' +
    '- missing：缺少的關鍵資訊（最多 2 項）\n' +
    '- focus：用一句話寫出老師「這一則」真正想要的東西（例如「收玩具時不再丟積木的具體做法」「怎麼跟家長開口」），補充或回答追問時寫整個個案目前要解決的事\n' +
    '格式：{"intent":"新個案","focus":"","referral":{"category":"none","level":"none","reason":""},"topics":[],"in_scope":true,"info_enough":true,"missing":[]}\n\n' +
    (prevTeacher.length ? '老師先前說過（已去識別化）：\n' + prevTeacher.join('\n') + '\n\n' : '') +
    '老師這一則訊息：\n' + latest;
}

function runChat_(req, opts) {
  opts = opts || {};
  const t0 = Date.now(), trace = [];
  // kind 讓觀測面板知道怎麼呈現：triage、retrieval、checks、skills、prompt、json、text
  const T = (step, detail, kind) => trace.push({ step: step, kind: kind || (typeof detail === 'string' ? 'text' : 'json'), detail: detail, ms: Date.now() - t0 });
  const metrics = { retrieved: null, top: null, cited: null, skills: [] };
  const stab = stabReset_();
  const quality = { gap: null, l5: [] };   // 知識不足與 L5 發現的問題，寫進對話紀錄「品質問題」欄做統計
  const C = cfgAll_();
  const maxFu = Number(C.max_followups == null ? 1 : C.max_followups);

  // 1. P03 去識別化
  const d = deid_(req.message || '', req.nameMap);
  const history = (req.history || []).slice(-8);
  const prevTeacher = history.filter(h => h.role === 'teacher').map(h => h.text);
  const fullCase = prevTeacher.concat([d.text]).join('\n');
  T('P03 去識別化', Object.keys(d.map).length ? JSON.stringify(d.map) : '沒有偵測到幼兒名字');

  // 2. L4 分流 ‖ P06 查詢向量（fetchAll 並行）；向量同時算「整段個案」與「只有最新訊息」，分流後再選用
  const skills = skills_();
  const topics = Array.from(new Set([].concat.apply([], skills.filter(s => !s.always).map(s => s.tags))));
  const refTable = referral_();
  const expAll = expandQuery_(fullCase), expNew = expandQuery_(d.text);
  // enable_triage 關閉：不呼叫分流 AI，主題改用關鍵字比對（Skill 對應主題、同義詞概念），直接檢索知識庫回答
  //  代價：不偵測轉介、不判斷超出範圍、不追問、不判斷是否換了新個案（同一段對話都當同一個案）
  const useTriage = truthy_(C.enable_triage == null || C.enable_triage === '' ? true : C.enable_triage);
  let tri, vecs;
  if (useTriage) {
    const triReq = genReq_(C.model_triage, [{ text: triagePrompt_(prevTeacher, d.text, topics, Object.keys(refTable)) }], { json: true, temperature: 0, noThink: true });
    const [triRes, embRes] = UrlFetchApp.fetchAll([triReq, embedReqs_([expAll.text, expNew.text], 'RETRIEVAL_QUERY')[0]]);
    stabAt_('L4');
    tri = parseJson_(genText_(genFix_(triReq, triRes)));
    vecs = embedParse_(embRes);
    validateTriage_(tri);
  } else {
    vecs = embedParse_(UrlFetchApp.fetch.apply(null, reqArgs_(embedReqs_([expAll.text, expNew.text], 'RETRIEVAL_QUERY')[0])));
    const words = fullCase + '\n' + expAll.concepts.join('\n');
    tri = { intent: prevTeacher.length ? '補充同一個案' : '新個案', focus: '', referral: { category: 'none', level: 'none' },
      topics: topics.filter(t => t && words.indexOf(t) >= 0), in_scope: true, info_enough: true, missing: [], skipped: true };
  }
  const ref = tri.referral && tri.referral.category && tri.referral.category !== 'none' && tri.referral.level && tri.referral.level !== 'none' ? tri.referral : null;
  if (useTriage) T('L4 分流', tri, 'triage');
  else T('L4 分流（已關閉）', '未呼叫分流 AI；主題依關鍵字比對：' + (tri.topics.join('、') || '無') + '｜不偵測轉介、不追問、同一段對話視為同一個案');

  // 意圖：新個案就只看這一則，舊的對話不帶進來；其他意圖延續整段個案
  const intent = INTENTS.indexOf(tri.intent) >= 0 ? tri.intent : (prevTeacher.length ? '補充同一個案' : '新個案');
  const isNew = intent === '新個案' || !prevTeacher.length;
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
  if (intent === '與幼教無關' || tri.in_scope === false) {
    T('P04 狀態機', '超出服務範圍（' + intent + '）');
    return finish({ type: 'out_of_scope', message: text_('msg_out_of_scope') });
  }
  const pick = skills.filter(s => s.always || s.tags.some(t => (tri.topics || []).indexOf(t) >= 0));
  if (!pick.some(s => !s.always)) { const def = skills.find(s => s.name === C.default_skill); if (def) pick.push(def); }
  const sysPrompt = String(C.system_prompt || '');
  const noTopic = !pick.some(s => !s.always);   // 沒有對應的主題 Skill，且 default_skill 留空
  const skillText = pick.map(s => '【' + s.name + '】\n' + s.content).join('\n\n') +
    (noTopic ? (pick.length ? '\n\n' : '') + '（這題沒有對應的主題 Skill：依老師的問題本身與檢索到的知識直接回答，不必套用特定框架。）' : '');
  metrics.skills = pick.map(s => s.name);

  if (intent !== '要求特定協助' && !tri.info_enough && fuDone < maxFu) {
    T('P04 狀態機', '資訊不足 → 追問');
    T('P05 Skill 載入', pick.map(s => ({ name: s.name, version: s.version, always: s.always })), 'skills');
    const fu = genJson_(C.model_answer, sysPrompt + '\n\n' + skillText + '\n\n' + text_('followup_prompt') + '\n目前缺少：' + ((tri.missing || []).join('、') || '情境細節') +
      '\n只輸出 JSON：{"empathy":"","questions":["",""]}\n\n老師的描述：\n' + caseText, { temperature: 0.4, noThink: true });
    T('A1 追問', fu);
    return finish({ type: 'followup', empathy: str_(fu.empathy), questions: (fu.questions || []).map(str_).slice(0, 2), followups: fuDone + 1 });
  }

  // 前幾輪給完整回答；之後（或使用者點選特定協助）改成「延續回覆」：只回應新內容，並提供下一步選項
  const fullRounds = Number(C.full_answer_rounds == null || C.full_answer_rounds === '' ? 2 : C.full_answer_rounds);
  const mode = intent === '要求特定協助' && !isNew ? 'continue' : (answered >= fullRounds ? 'continue' : 'full');
  T('P04 狀態機', intent + '｜' + (tri.info_enough ? '資訊足夠' : '已追問過') + '｜已完整回答 ' + answered + ' 次 → ' +
    (mode === 'full' ? '完整回答' : '延續回覆') + (ref ? '（附轉介：' + ref.category + '・' + ref.level + '）' : ''));
  T('P05 Skill 載入', pick.map(s => ({ name: s.name, version: s.version, always: s.always })), 'skills');

  // 4. P06 檢索
  const focus = str_(tri.focus) || d.text;
  const poolSize = Math.max(Number(C.rerank_pool || 12), Number(C.top_k || 6));
  const sd = searchDetail_(qvec, caseText, exp.concepts, null, poolSize);
  metrics.top = sd.cands.length ? sd.cands[0].score : 0;
  T('P06 檢索', { minScore: sd.minScore, topK: sd.topK, total: sd.total, cands: sd.cands }, 'retrieval');
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
  const range = mode === 'full' ? [2, 3] : [0, 2];
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
      ? assemble_(sysPrompt, skillText, tri, caseText, hits, ctxHistory, ref, refWho, fb, focus, fallback)
      : assembleContinue_(sysPrompt, skillText, tri, prevTeacher, d.text, hits, ctxHistory, ref, refWho, intent, fb, focus, fallback);
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
    ans = cleanAnswer_(raw);
    T(attempt ? 'A1 重寫' : 'A1 生成', '策略 ' + (ans.strategies || []).length + ' 個' + (kg && kg !== 'null' ? '｜模型回報知識不足：' + kg : ''));
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
  const used = {}; (ans.strategies || []).forEach(s => (s.refs || []).forEach(r => used[r] = true));
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
  if ('intent' in t && INTENTS.indexOf(t.intent) < 0) stabNote_('選項不符', 'intent＝' + t.intent);
  if ('topics' in t && !Array.isArray(t.topics)) stabNote_('型別錯誤', 'topics 不是陣列');
}
function validateAnswer_(a, mode) {
  if (!a || typeof a !== 'object' || Array.isArray(a)) return stabNote_('型別錯誤', '回答不是物件');
  const req = mode === 'full' ? ['empathy', 'understanding', 'strategies', 'parent_message', 'observe', 'teacher_care', 'referral'] : ['reply', 'strategies', 'parent_message', 'observe', 'referral'];
  req.forEach(k => { if (!(k in a)) stabNote_('缺欄位', k); });
  ['empathy', 'understanding', 'reply', 'parent_message', 'teacher_care'].forEach(k => { if (a[k] != null && typeof a[k] !== 'string') stabNote_('型別錯誤', k + ' 不是文字'); });
  if ('strategies' in a && !Array.isArray(a.strategies)) stabNote_('型別錯誤', 'strategies 不是陣列');
  if ('observe' in a && a.observe != null && !Array.isArray(a.observe)) stabNote_('型別錯誤', 'observe 不是陣列');
  [].concat(Array.isArray(a.strategies) ? a.strategies : []).forEach((s, i) => {
    if (!s || typeof s !== 'object') return stabNote_('型別錯誤', '策略' + (i + 1) + ' 不是物件');
    ['title', 'how', 'refs'].forEach(k => { if (!(k in s)) stabNote_('缺欄位', '策略' + (i + 1) + '.' + k); });
    if ('refs' in s && !Array.isArray(s.refs)) stabNote_('型別錯誤', '策略' + (i + 1) + '.refs 不是陣列');
  });
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
function cleanAnswer_(a) {
  a = a || {};
  ['empathy', 'understanding', 'parent_message', 'teacher_care', 'referral', 'reply'].forEach(k => {
    if (a[k] == null || a[k] === 'null') a[k] = null; else a[k] = stripRefs_(a[k]) || null;
  });
  // 型別容錯：refs 給成字串、strategies／observe 不是陣列時也不會出錯
  a.strategies = [].concat(a.strategies || []).filter(s => s && typeof s === 'object').map(s => ({ title: stripRefs_(s.title), how: stripRefs_(s.how), refs: [].concat(s.refs || []).map(String).filter(Boolean) }));
  a.observe = [].concat(a.observe || []).map(stripRefs_).filter(Boolean);
  if (a.options) a.options = [].concat(a.options).map(str_).filter(Boolean);
  return a;
}
function continueOptions_() {
  return text_('continue_options').split(/\n+/).map(s => s.replace(/^[-・\d.、\s]+/, '').trim()).filter(Boolean);
}

/** 延續回覆：已經給過完整建議，這次只回應新的內容或使用者要的那件事 */
function assembleContinue_(sys, skillText, tri, prevTeacher, latest, hits, history, ref, refWho, intent, feedback, focus, fallback) {
  const forbiddenWords = forbidden_().filter(f => !f.regex).map(f => f.rule);
  const hist = history.map(h => (h.role === 'teacher' ? '老師：' : '助手：') + h.text).join('\n');
  const kn = hits.map(h => h.ref + '｜' + h.title + (h.page ? '（第' + h.page + '頁）' : '') + '\n' + h.text).join('\n\n');
  return [
    '【1 固定 system prompt】\n' + sys,
    '【2 主題 Skill】\n' + skillText,
    '【3 對話到目前為止】\n' + (hist || '（無）'),
    '【4 老師這次說的】（' + intent + '）\n' + latest + (focus ? '\n老師想要的：' + focus : ''),
    fallback
      ? '【5 知識庫內容不足】' + (kn ? '以下知識沒有直接針對這次的問題，只能當背景引用（編號 K1…）：\n' + kn + '\n' : '') +
        '這次可以用一般實務知識補足：只提供低風險、日常可做的做法；不得推測診斷；來自一般知識的策略 refs 填空陣列；最後建議把狀況整理給專業人員確認。'
      : kn ? '【5 檢索到的知識（只能引用這些，編號 K1…）】\n' + kn : '【5 檢索到的知識】這次沒有找到新的相關知識，不要給新的策略。',
    '【延續回覆的做法】\n' + text_('continue_prompt') + '\n可提供的下一步選項（挑 2–4 個最適合的，可改寫成符合情境的說法）：\n' + continueOptions_().map(o => '- ' + o).join('\n'),
    feedback ? '【重寫指示】上一版沒有通過檢查：' + feedback.join('；') + '。請修正後重寫。' : '',
    '輸出格式（只輸出 JSON，沒有內容的欄位填 null 或空陣列）：{"reply":"直接回應老師這次說的話","strategies":[{"title":"","how":"","refs":["K1"]}],"parent_message":null,"observe":[],"referral":' +
      (ref ? '"依「' + ref.level + '」簡短說明該找誰（可找：' + refWho + '）"' : 'null') + ',"options":["下一步選項"],"knowledge_gap":null}\n' +
      (fallback ? '' : '若這次的問題換了對象或主題，而上面的知識沒有在談它，不要硬套：knowledge_gap 填一句話說明缺少什麼；知識有涵蓋時為 null。\n') +
      '規則：strategies 0–2 個，只放之前沒給過的新做法，' + (fallback ? '有引用知識才填 refs；' : '每個都要有 refs；') + '不要重複之前回答過的內容；不得推測診斷；保留「幼兒A」等代稱；refs 編號不要寫進文字裡。' +
      (forbiddenWords.length ? '不得出現這些詞：' + forbiddenWords.join('、') + '。' : ''),
  ].filter(Boolean).join('\n\n');
}

function assemble_(sys, skillText, tri, caseText, hits, history, ref, refWho, feedback, focus, fallback) {
  const forbiddenWords = forbidden_().filter(f => !f.regex).map(f => f.rule);
  const hist = history.map(h => (h.role === 'teacher' ? '老師：' : '助手：') + h.text).join('\n');
  const kn = hits.map(h => h.ref + '｜' + h.title + (h.page ? '（第' + h.page + '頁）' : '') + '\n' + h.text).join('\n\n');
  return [
    '【1 固定 system prompt】\n' + sys,
    '【2 主題 Skill】\n' + skillText,
    '【3 情境資訊】\n分流結果：' + JSON.stringify(tri) + '\n老師原話（已去識別化）：\n' + caseText +
      (focus ? '\n\n【老師這次想要的】' + focus + '\n回答要先直接回應這一點；策略要具體到這個孩子與情境（做什麼、什麼時候、怎麼說），不要只寫一般原則。' : ''),
    fallback
      ? '【4 知識庫內容不足】' + (kn ? '以下只是相關的背景或原則，可以引用（編號 K1…），但沒有直接針對這個情況的做法：\n' + kn : '知識庫沒有相關內容。') +
        '\n這次可以用一般幼教實務知識補足：只能提供低風險、班級日常可做的做法（環境調整、預告、示範、正向回饋、觀察記錄等）；不得判斷行為功能、介入層級或推測診斷；來自一般知識的策略 refs 填空陣列；最後建議老師把狀況整理給專業人員確認。'
      : '【4 檢索到的知識（只能引用這些，編號 K1…）】\n' + kn,
    hist ? '【5 對話歷史】\n' + hist : '',
    feedback ? '【重寫指示】上一版沒有通過檢查：' + feedback.join('；') + '。請修正後重寫。' : '',
    '輸出格式（只輸出 JSON）：{"empathy":"先回應老師感受的一兩句","understanding":"看見與理解：把行為轉成可觀察事件，推想背後的需要（兩三句）","strategies":[{"title":"策略名稱","how":"具體怎麼做（一兩句）","refs":["K1"]}],"parent_message":"若牽涉家長，給家長的訊息草稿，否則 null","observe":["接下來的觀察重點"],"teacher_care":"若老師提到壓力，一兩句自我照顧建議，否則 null","knowledge_gap":null,"referral":' +
      (ref ? '"依「' + ref.level + '」說明為什麼需要、該找誰（可找：' + refWho + '）"' : 'null') + '}\n' +
      (fallback ? '' : '若上面的知識沒有在談這個問題的對象或情況（例如問的是某種障礙、知識談的是另一種），不要硬套：knowledge_gap 填一句話說明缺少什麼，strategies 給空陣列；知識有涵蓋時 knowledge_gap 為 null。\n') +
      '規則：' + (fallback ? 'strategies 2–3 個；引用背景知識時 refs 只能用上面提供的 K 編號，來自一般知識時 refs 為空陣列；不得推測診斷或使用標籤化用語；保留「幼兒A」等代稱。' : text_('answer_rules')) +
      (forbiddenWords.length ? '回答中不得出現這些詞（即使是否定句也不要寫）：' + forbiddenWords.join('、') + '。' : ''),
  ].filter(Boolean).join('\n\n');
}

// ---------- 檢查：P08 規則、P09 出處、L5 依據與轉介 ----------
function checks_(caseText, ans, hits, C, range, focus, fallback) {
  range = range || [2, 3];
  const items = [], reasons = []; let l5Issues = [], relFail = null;
  const s = JSON.stringify(ans);
  const bad = forbidden_().find(f => f.regex ? new RegExp(f.rule).test(s) : s.indexOf(f.rule) >= 0);
  items.push({ name: 'P08 規則檢查', pass: !bad, note: bad ? '出現禁止用語「' + bad.rule + '」' : '沒有禁止用語' });
  if (bad) reasons.push('不可使用「' + bad.rule + '」' + (bad.note ? '（' + bad.note + '）' : ''));
  const ok = {}; hits.forEach(h => ok[h.ref] = true);
  const st = ans.strategies || []; const p9 = [];
  if (st.length < range[0] || st.length > range[1]) p9.push('策略數量 ' + st.length + ' 個，應為 ' + range[0] + '–' + range[1] + ' 個');
  st.forEach(x => { if (!fallback && !(x.refs || []).length) p9.push('「' + x.title + '」沒有出處'); (x.refs || []).forEach(r => { if (!ok[r]) p9.push(r + ' 不在檢索結果中'); }); });
  items.push({ name: 'P09 出處核對', pass: !p9.length, note: p9.join('；') || (fallback ? '知識不足，部分策略來自 AI 一般知識（已標示）' : st.length ? '每條建議都有出處' : '沒有新策略') });
  reasons.push.apply(reasons, p9);
  // L5 依據與轉介（可關閉）＋回應度（可另外關閉）；兩者合併為一次 AI 呼叫
  const doL5 = truthy_(C.enable_llm_check);
  const doRel = !!focus && truthy_(C.enable_relevance_check == null || C.enable_relevance_check === '' ? true : C.enable_relevance_check);
  if (doL5 || doRel) {
    try {
      const kn = hits.map(h => h.ref + '：' + h.text).join('\n');
      const l5 = genJson_(C.model_check, '你是學前融合教育 AI 回答的審查器，只輸出 JSON。\n' +
        (doL5 ? text_('check_prompt') + '\n每個問題的 severe：符合上面「嚴重」的定義為 true，其他為 false。\n' : '') +
        (doL5 && fallback ? '這次知識庫內容不足，refs 為空的策略來自一般實務知識：「依據」改為檢查是否屬於低風險、班級日常可做的做法，且沒有判斷行為功能、介入層級或推測診斷；越界或可能造成傷害才算嚴重。\n' : '') +
        (doRel ? '另外判斷回應度：回答是否直接回應「老師想要的」，策略是否具體到這個孩子與情境（不是只有一般原則）。addressed 為 true／false，note 說明缺了什麼；' +
          'addressed 為 false 時，cause 填「知識不足」（提供的知識沒有在談這個問題的對象或情況，重寫也無法改善）或「偏題」（知識有涵蓋，但回答沒用好）。\n' : '') +
        '格式：{' + (doL5 ? '"issues":[{"type":"依據|轉介|語氣","severe":false,"note":"一句話說明問題"}]' : '') + (doL5 && doRel ? ',' : '') +
        (doRel ? '"relevance":{"addressed":true,"cause":null,"note":""}' : '') + '}' + (doL5 ? '，沒有問題時 issues 為空陣列' : '') + '\n\n' +
        (doRel ? '老師想要的：' + focus + '\n\n' : '') + '老師描述：\n' + caseText + '\n\n知識：\n' + kn + '\n\n回答：\n' + s, { temperature: 0, noThink: true });
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
        items.push({ name: 'L5 依據與轉介', pass: pass,
          note: !issues.length ? '有依據、無遺漏轉介' : (pass ? '小問題（不擋）：' : '') + issues.map(i => (i.severe ? '【嚴重】' : '') + txt(i)).join('；') });
        if (!pass) reasons.push.apply(reasons, severe.length ? severe.map(txt) : ['依據或轉介有問題']);
      }
      if (doRel) {
        const rel = l5.relevance || {};
        const ok2 = rel.addressed !== false;
        if (!ok2) relFail = { focus: focus, note: str_(rel.note).slice(0, 160), cause: rel.cause === '知識不足' ? '知識不足' : '偏題' };
        items.push({ name: '回應度', pass: ok2, note: ok2 ? '有直接回應：' + focus : '【嚴重】沒有回應到「' + focus + '」' + (rel.note ? '：' + str_(rel.note) : '') });
        if (!ok2) reasons.push('沒有直接回應老師想要的「' + focus + '」' + (rel.note ? '（' + str_(rel.note) + '）' : '') + '，請先直接回應這一點，策略要具體');
      }
    } catch (e) { items.push({ name: doL5 ? 'L5 依據與轉介' : '回應度', pass: true, note: '檢查未完成（略過）：' + e.message }); }
  }
  return { pass: items.every(i => i.pass), items: items, reasons: reasons, l5: l5Issues, relFail: relFail };
}

function conservativeMsg_(refTable, why) {
  const r = refTable['系統能力不足'] || {};
  return text_(why === 'check' ? 'msg_check_failed' : 'msg_no_knowledge').split('{who}').join(r.who || '園長或協同老師');
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
