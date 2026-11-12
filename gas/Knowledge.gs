/** 知識庫：文件轉文字、切段、向量化、檢索（P06、P11） */

// ---------- 文件轉文字 ----------
function extractText_(name, mimeType, b64) {
  const lower = String(name || '').toLowerCase();
  const bytes = Utilities.base64Decode(b64);
  if (/\.(txt|md|csv)$/.test(lower) || /^text\//.test(mimeType || '')) {
    return Utilities.newBlob(bytes).getDataAsString('UTF-8');
  }
  if (/\.pdf$/.test(lower) || mimeType === 'application/pdf') {
    const prompt = '請把這份 PDF 的文字完整轉錄出來，保留標題與段落，不要摘要、不要改寫。每一頁開頭加上一行「[[第N頁]]」。表格用文字逐列轉錄。只輸出轉錄內容。';
    const req = genReq_(cfg_('model_answer'), [{ text: prompt }, { inline_data: { mime_type: 'application/pdf', data: b64 } }], { temperature: 0, maxTokens: 65536 }); const a = reqArgs_(req);
    return genText_(genFix_(req, UrlFetchApp.fetch(a[0], a[1])));
  }
  if (/\.docx?$/.test(lower)) {
    // 需要在 Apps Script 開啟「Drive API」進階服務（v3）
    const blob = Utilities.newBlob(bytes, mimeType || 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', name);
    const file = Drive.Files.create({ name: 'tmp-' + name, mimeType: 'application/vnd.google-apps.document' }, blob);
    try { return DocumentApp.openById(file.id).getBody().getText(); }
    finally { DriveApp.getFileById(file.id).setTrashed(true); }
  }
  throw new Error('不支援的檔案格式，請上傳 PDF、Word（.docx）、TXT 或 Markdown');
}

// ---------- 切段：依段落累積到約 size 字，保留頁碼 ----------
function chunkText_(text, size, overlap) {
  size = size || Number(cfg_('chunk_size') || 500);
  overlap = overlap || Number(cfg_('chunk_overlap') || 80);
  const paras = String(text).replace(/\r/g, '').split(/\n\s*\n|\n(?=\[\[第\d+頁\]\])/).map(s => s.trim()).filter(Boolean);
  const out = []; let buf = '', page = '', bufPage = '';
  const flush = () => { if (buf.trim()) out.push({ text: buf.trim(), page: bufPage || page }); };
  paras.forEach(p => {
    const m = p.match(/^\[\[第(\d+)頁\]\]\s*/);
    if (m) { page = m[1]; p = p.slice(m[0].length).trim(); if (!p) return; }
    if (!buf) bufPage = page;
    if ((buf + '\n' + p).length > size && buf) {
      flush();
      buf = buf.slice(-overlap) + '\n' + p; bufPage = page;
    } else buf = buf ? buf + '\n' + p : p;
    while (buf.length > size * 1.6) { out.push({ text: buf.slice(0, size), page: bufPage || page }); buf = buf.slice(size - overlap); }
  });
  flush();
  return out;
}

// ---------- 段落類型：用來限制「原則／背景」這類後設知識的比例 ----------
const CHUNK_TYPES = ['具體做法', '案例', '溝通範例', '原則／背景', '轉介資訊'];
const ACTIONABLE = ['具體做法', '案例', '溝通範例'];
/** AI 分類段落類型；失敗的留空（視為未分類，不受配額限制） */
function classifyChunks_(texts) {
  const out = texts.map(() => '');
  for (let i = 0; i < texts.length; i += 25) {
    const group = texts.slice(i, i + 25);
    try {
      const r = genJson_(cfg_('model_triage'), '把每一段幼教知識分成一種類型，只輸出 JSON，types 陣列順序與段落相同。類型：' + JSON.stringify(CHUNK_TYPES) +
        '。具體做法＝老師可以直接照做的步驟或策略；案例＝具體情境的處理實例；溝通範例＝可直接使用的說法或訊息；原則／背景＝理論、原則、概念說明；轉介資訊＝找誰、流程、資源。\n' +
        '格式：{"types":["具體做法","原則／背景",…]}\n\n' + group.map((t, j) => '【段落' + (j + 1) + '】\n' + String(t).slice(0, 700)).join('\n\n'), { temperature: 0, noThink: true });
      (Array.isArray(r) ? r : (r.types || Object.values(r).find(Array.isArray) || [])).forEach((t, j) => { if (CHUNK_TYPES.indexOf(t) >= 0) out[i + j] = t; });
    } catch (e) { console.warn('分類失敗', e); }
  }
  return out;
}

// ---------- 新增文件：轉文字 → 切段 → 向量 → 寫入（狀態：待審核） ----------
function addDocument_(meta, text, user) {
  if (!String(text || '').trim()) throw new Error('沒有讀到任何文字');
  const docId = 'D' + Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyMMddHHmmss');
  const chunks = chunkText_(text);
  const vecs = embedTexts_(chunks.map(c => (meta.title ? meta.title + '\n' : '') + c.text), 'RETRIEVAL_DOCUMENT');
  const now = new Date();
  const types = classifyChunks_(chunks.map(c => c.text));
  append_('chunks', chunks.map((c, i) => [docId + '-' + String(i + 1).padStart(3, '0'), docId, i + 1, c.text, c.page || '', quant_(vecs[i]), '待審核', types[i]]));
  append_('docs', [[docId, meta.title || '', meta.source || '', meta.evidence || '', meta.fileName || '', chunks.length, '待審核', user || '', now, '', '']]);
  // 有匯入知識圖譜時，順便把段落對應到節點、提出新節點候選（失敗不影響上傳）
  let graph = null;
  const nm = cfg_('enable_node_mapping');
  if (truthy_(nm) && kgHasGraph_()) {
    try { graph = kgMapDocument_(docId); } catch (e) { graph = { error: String(e.message || e) }; }
  }
  return { docId: docId, chunks: chunks.length, graph: graph };
}

function setDocStatus_(docId, status, user) {
  const docs = rows_('docs');
  const d = docs.find(r => String(r['文件ID']) === docId);
  if (!d) throw new Error('找不到文件');
  const s = sh_('docs');
  const head = s.getRange(1, 1, 1, s.getLastColumn()).getValues()[0];
  s.getRange(d._row, head.indexOf('狀態') + 1).setValue(status);
  s.getRange(d._row, head.indexOf('審核者') + 1).setValue(user || '');
  s.getRange(d._row, head.indexOf('審核時間') + 1).setValue(new Date());
  // 同步段落狀態
  const cs = sh_('chunks'); const v = cs.getDataRange().getValues(); const ch = v[0];
  const di = ch.indexOf('文件ID'), si = ch.indexOf('狀態');
  if (v.length < 2) return;
  for (let r = 1; r < v.length; r++) if (String(v[r][di]) === docId) v[r][si] = status;
  cs.getRange(2, si + 1, v.length - 1, 1).setValues(v.slice(1).map(r => [r[si]]));
}

// ---------- 檢索（P06）：同義詞擴充 + 向量相似度 + 關鍵字加分 ----------
function expandQuery_(text) {
  const hits = synonyms_().filter(s => text.indexOf(s.word) >= 0);
  const concepts = Array.from(new Set(hits.map(h => h.concept)));
  return { text: concepts.length ? text + '\n相關概念：' + concepts.join('、') : text, concepts: concepts, words: hits.map(h => h.word) };
}

function loadIndex_() {
  const docs = {};
  rows_('docs').forEach(r => docs[String(r['文件ID'])] = { title: String(r['標題']), source: String(r['出處']), evidence: String(r['證據等級']) });
  const v = sh_('chunks').getDataRange().getValues(); const h = v.shift();
  const I = n => h.indexOf(n);
  const dim = Number(cfg_('embed_dim') || 768);
  return v.filter(r => r[I('狀態')] === '已核可' && r[I('向量')]).map(r => {
    const vec = vecOf_(r[I('向量')], dim); if (!vec) return null;   // 向量損壞的段落先略過（到後台「檢查並修復向量」重算）
    return { id: String(r[I('段落ID')]), docId: String(r[I('文件ID')]), text: String(r[I('內容')]), page: String(r[I('頁碼')] || ''),
      vec: vec, doc: docs[String(r[I('文件ID')])] || {}, type: I('類型') >= 0 ? String(r[I('類型')] || '') : '' };
  }).filter(Boolean);
}
/** 讀出儲存格裡的向量；格式錯誤（例如被當成公式變成 #ERROR!）或維度不符回傳 null */
function vecOf_(cell, dim) {
  const s = String(cell || '').replace(/^'/, '');
  if (!s || s.charAt(0) === '#') return null;
  try { const v = dequant_(s); return v && v.length === dim ? v : null; } catch (e) { return null; }
}
/** 找出向量損壞的段落並重新產生向量（舊版寫入時，以「+」開頭的 base64 會被試算表當成公式而損壞） */
function repairVectors_() {
  const dim = Number(cfg_('embed_dim') || 768);
  const docs = {}; rows_('docs').forEach(r => docs[String(r['文件ID'])] = String(r['標題'] || ''));
  const s = sh_('chunks'); const head = s.getRange(1, 1, 1, s.getLastColumn()).getValues()[0]; const vi = head.indexOf('向量') + 1;
  const bad = rows_('chunks').filter(r => !vecOf_(r['向量'], dim));
  if (!bad.length) return { total: rows_('chunks').length, bad: 0, fixed: 0 };
  let fixed = 0;
  for (let i = 0; i < bad.length; i += 50) {
    const group = bad.slice(i, i + 50);
    const vecs = embedTexts_(group.map(r => (docs[String(r['文件ID'])] ? docs[String(r['文件ID'])] + '\n' : '') + String(r['內容'])), 'RETRIEVAL_DOCUMENT');
    group.forEach((r, j) => { if (vecs[j]) { s.getRange(r._row, vi).setValue(safeCell_(quant_(vecs[j]))); fixed++; } });
  }
  try { CacheService.getScriptCache().remove('index'); } catch (e) {}
  return { total: rows_('chunks').length, bad: bad.length, fixed: fixed, ids: bad.slice(0, 20).map(r => String(r['段落ID'])) };
}

/** 檢索明細：回傳門檻、前 10 名候選（含未達門檻者）與最後採用的段落；觀測面板與命中率測試共用 */
function searchDetail_(qvec, rawText, concepts, k, pool) {
  const q = normVec_(qvec).map(x => Math.round(x * 127));
  const keys = Array.from(new Set((concepts || []).concat(String(rawText).match(/[一-鿿]{2,4}/g) || []))).slice(0, 30);
  const minScore = Number(cfg_('min_score') || 0.45);
  const topK = pool || k || Number(cfg_('top_k') || 6);
  const scored = loadIndex_().map(c => {
    let dot = 0; const v = c.vec;
    for (let i = 0; i < q.length && i < v.length; i++) dot += q[i] * v[i];
    let kw = 0; keys.forEach(w => { if (c.text.indexOf(w) >= 0) kw++; });
    const vecScore = dot / (127 * 127);
    return { c: c, vec: vecScore, kw: Math.min(kw, 5), score: vecScore + Math.min(kw, 5) * 0.01 };
  }).sort((a, b) => b.score - a.score);
  const r3 = x => Math.round(x * 1000) / 1000;
  const hits = scored.filter(x => x.score >= minScore).slice(0, topK)
    .map((x, i) => ({ ref: 'K' + (i + 1), id: x.c.id, docId: x.c.docId, title: x.c.doc.title, source: x.c.doc.source, evidence: x.c.doc.evidence, page: x.c.page, text: x.c.text, score: r3(x.score), type: x.c.type }));
  const refOf = {}; hits.forEach(h => refOf[h.id] = h.ref);
  const cands = scored.slice(0, 10).map((x, i) => ({ rank: i + 1, ref: refOf[x.c.id] || '', id: x.c.id, docId: x.c.docId, title: x.c.doc.title, page: x.c.page,
    score: r3(x.score), vec: r3(x.vec), kw: x.kw, pass: !!refOf[x.c.id], text: x.c.text.slice(0, 300), type: x.c.type }));
  return { minScore: minScore, topK: topK, total: scored.length, cands: cands, hits: hits };
}

/** qvec 為查詢向量（未壓縮）；回傳前 k 段 */
function search_(qvec, rawText, concepts, k) { return searchDetail_(qvec, rawText, concepts, k).hits; }

// ---------- 重新排序與配額：以「能不能回答提問焦點」排序，限制後設知識比例 ----------
/**
 * pool：向量檢索的候選（已過門檻）；回傳最後採用的段落（重新編號 K1…）與給觀測面板的明細
 * 分數：3 直接回答焦點、2 部分回答、1 只是背景相關、0 無關（0 分不採用）
 */
function rerankSelect_(focus, caseText, pool, C) {
  const topK = Number(C.top_k || 6), minAct = Number(C.quota_min_actionable == null || C.quota_min_actionable === '' ? 3 : C.quota_min_actionable);
  const maxPri = Number(C.quota_max_principle == null || C.quota_max_principle === '' ? 2 : C.quota_max_principle);
  let scores = pool.map(() => null), used = false, why = '';
  const rrOn = truthy_(C.enable_rerank == null || C.enable_rerank === '' ? true : C.enable_rerank);
  if (!rrOn) why = '重排已關閉';
  if (pool.length && truthy_(C.enable_rerank == null || C.enable_rerank === '' ? true : C.enable_rerank)) {
    try {
      stabAt_('重排');
      const r = genJson_(C.model_rerank || C.model_check || C.model_triage, '你在替幼教老師的提問挑選參考資料，只輸出 JSON。\n' +
        '老師這次想要的（提問焦點）：' + focus + '\n老師的描述：' + String(caseText).slice(0, 1500) + '\n\n' +
        '為每段資料打分：3＝能直接回答焦點（有具體可用的做法）、2＝部分回答、1＝只是相關背景或一般原則、0＝無關。\n' +
        '注意：焦點若指名特定對象或情況（例如某種障礙類別、年齡、場合），段落必須是在談這個對象或情況才能給 2 或 3；談的是別的對象，即使做法看起來可以套用，也只能給 1。\n' +
        '格式：{"scores":[3,1,…]}，順序與段落相同。\n\n' + pool.map((h, i) => '【' + (i + 1) + '】' + (h.type ? '（' + h.type + '）' : '') + h.text.slice(0, 600)).join('\n\n'),
        { temperature: 0, noThink: true });
      const arr = Array.isArray(r) ? r : (r.scores || []);
      if (!Array.isArray(r) && !Array.isArray(r.scores)) stabNote_('缺欄位', 'scores');
      else if (arr.length !== pool.length) stabNote_('數量不符', '分數 ' + arr.length + ' 個／段落 ' + pool.length + ' 段');
      scores = pool.map((_, i) => { const v = Number(arr[i]); return arr[i] == null || isNaN(v) ? null : Math.max(0, Math.min(3, v)); });
      used = scores.some(v => v != null);          // 讀不出分數就退回向量順序
      if (!used) scores = pool.map(() => null);
      if (!used) why = '讀不出分數';
    } catch (e) { why = '重排失敗：' + String(e.message || e).slice(0, 120); stabNote_('重排失敗', e.message); console.warn('重新排序失敗，改用向量順序', e); }
  }
  const items = pool.map((h, i) => ({ h: h, s: scores[i], i: i }));
  // 排序：重排分數高者優先，同分看向量分數；沒有重排分數時照向量順序
  const order = items.filter(x => x.s == null || x.s > 0).sort((a, b) => (b.s == null ? 1.5 : b.s) - (a.s == null ? 1.5 : a.s) || b.h.score - a.h.score);
  const pick = [], isAct = x => ACTIONABLE.indexOf(x.h.type) >= 0, isPri = x => x.h.type === '原則／背景';
  order.filter(isAct).slice(0, Math.min(minAct, topK)).forEach(x => pick.push(x));               // 先保留具體做法的名額
  order.forEach(x => {
    if (pick.length >= topK || pick.indexOf(x) >= 0) return;
    if (isPri(x) && pick.filter(isPri).length >= maxPri) return;                                   // 原則／背景最多 maxPri 段
    pick.push(x);
  });
  pick.sort((a, b) => order.indexOf(a) - order.indexOf(b));
  const hits = pick.map((x, n) => Object.assign({}, x.h, { ref: 'K' + (n + 1), rerank: x.s }));
  const direct = used ? pick.filter(x => x.s >= 2).length : pick.length;
  return {
    hits: hits, direct: direct, actionable: pick.filter(isAct).length,
    trace: { used: used, why: why, model: C.model_rerank || C.model_check || C.model_triage, focus: focus, topK: topK, minAct: minAct, maxPri: maxPri,
      items: items.map(x => ({ id: x.h.id, title: x.h.title, page: x.h.page, type: x.h.type || '', vec: x.h.score, rerank: x.s,
        ref: (hits.find(h => h.id === x.h.id) || {}).ref || '', text: x.h.text.slice(0, 200) })) },
  };
}
