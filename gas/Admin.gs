/**
 * 後台兩種開法：
 *  1. 試算表選單「開啟後台（視窗）」：用 Google 帳號比對「管理員」的 Email 欄，不用登入
 *  2. 網頁 /exec?page=admin：用帳號密碼登入（帳號在「管理員」工作表，密碼只存雜湊），不需分享試算表
 */

function onOpen() {
  SpreadsheetApp.getUi().createMenu('AI 助手後台')
    .addItem('開啟後台（視窗）', 'openAdmin')
    .addItem('後台網頁網址', 'showAdminLink')
    .addItem('設定後台帳號密碼', 'setAdminPasswordUi')
    .addSeparator()
    .addItem('初始化工作表（setup）', 'setup')
    .addToUi();
}

function openAdmin() {
  const t = HtmlService.createTemplateFromFile('AdminUI'); t.webMode = false;
  SpreadsheetApp.getUi().showModalDialog(t.evaluate().setWidth(1280).setHeight(820), 'AI 助手後台');
}

/** 後台網頁（由 doGet ?page=admin 呼叫） */
function adminPage_() {
  const t = HtmlService.createTemplateFromFile('AdminUI'); t.webMode = true;
  return t.evaluate().setTitle('AI 助手後台').addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

/** 試算表選單：顯示後台網址 */
function showAdminLink() {
  requireAdmin_();
  let url = '';
  try { url = ScriptApp.getService().getUrl() || ''; } catch (e) {}
  url = url ? url.replace(/\/dev$/, '/exec') + '?page=admin' : '';
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const html = HtmlService.createHtmlOutput('<div style="font-family:sans-serif;font-size:14px;line-height:1.7">' +
    (url ? '<p>後台網址：<br><a href="' + esc(url) + '" target="_blank">' + esc(url) + '</a></p>' : '<p>還沒有部署網頁應用程式。部署後，網址是聊天頁用的 /exec 網址加上 <b>?page=admin</b>。</p>') +
    '<p style="color:#666">用「設定後台帳號密碼」建立的帳號登入。登入有效 6 小時。</p></div>').setWidth(560).setHeight(220);
  SpreadsheetApp.getUi().showModalDialog(html, '後台網址');
}

// ---------- 帳號密碼 ----------
const SESSION_SECONDS = 21600;        // 6 小時（CacheService 上限）
const LOGIN_MAX_FAIL = 5, LOGIN_LOCK_SECONDS = 900;
function hashPassword_(password, salt) {
  let h = salt + '|' + password;
  for (let i = 0; i < 200; i++) h = Utilities.base64Encode(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, h, Utilities.Charset.UTF_8));
  return h;
}
/** 試算表選單：新增帳號或重設密碼（只有管理員 Email 名單內的人能操作） */
function setAdminPasswordUi() {
  requireAdmin_();
  const ui = SpreadsheetApp.getUi();
  const a = ui.prompt('設定後台帳號密碼', '帳號（英數字，例如 liu 或 email）：', ui.ButtonSet.OK_CANCEL);
  if (a.getSelectedButton() !== ui.Button.OK) return;
  const account = a.getResponseText().trim();
  if (!/^[A-Za-z0-9._@-]{3,64}$/.test(account)) return ui.alert('帳號只能用英文、數字和 . _ @ -，3 到 64 個字');
  const p = ui.prompt('設定後台帳號密碼', '「' + account + '」的密碼（至少 10 個字）：', ui.ButtonSet.OK_CANCEL);
  if (p.getSelectedButton() !== ui.Button.OK) return;
  const pw = p.getResponseText();
  if (pw.length < 10) return ui.alert('密碼至少 10 個字');
  setAdminPassword_(account, pw);
  ui.alert('已設定「' + account + '」。後台網址可從選單「後台網頁網址」查看。');
}
function setAdminPassword_(account, pw) {
  const s = sh_('admins'); const head = s.getRange(1, 1, 1, s.getLastColumn()).getValues()[0];
  ['帳號', '密碼雜湊', '鹽'].forEach(h => { if (head.indexOf(h) < 0) { head.push(h); s.getRange(1, head.length).setValue(h).setFontWeight('bold'); } });
  const salt = Utilities.getUuid(), hash = hashPassword_(pw, salt);
  const r = rows_('admins').find(x => String(x['帳號'] || '').toLowerCase() === account.toLowerCase());
  const row = r ? r._row : s.getLastRow() + 1;
  if (!r) s.getRange(row, head.indexOf('備註') + 1).setValue('後台帳號');
  s.getRange(row, head.indexOf('帳號') + 1).setValue(account);
  s.getRange(row, head.indexOf('密碼雜湊') + 1).setValue(hash);
  s.getRange(row, head.indexOf('鹽') + 1).setValue(salt);
}
/** 網頁登入：成功回傳通行證（6 小時）；同一帳號連錯 5 次鎖 15 分鐘 */
function adminLogin(account, password) {
  account = String(account || '').trim(); password = String(password || '');
  if (!account || !password) throw new Error('請輸入帳號和密碼');
  const cache = CacheService.getScriptCache(), fk = 'loginfail_' + account.toLowerCase();
  const fails = Number(cache.get(fk) || 0);
  if (fails >= LOGIN_MAX_FAIL) throw new Error('嘗試太多次，請 15 分鐘後再試');
  const r = rows_('admins').find(x => String(x['帳號'] || '').toLowerCase() === account.toLowerCase());
  const ok = r && r['密碼雜湊'] && r['鹽'] && hashPassword_(password, String(r['鹽'])) === String(r['密碼雜湊']);
  if (!ok) { cache.put(fk, String(fails + 1), LOGIN_LOCK_SECONDS); throw new Error('帳號或密碼不正確'); }
  cache.remove(fk);
  const token = Utilities.getUuid() + Utilities.getUuid().replace(/-/g, '');
  cache.put('admsess_' + token, String(r['帳號']), SESSION_SECONDS);
  return { token: token, account: String(r['帳號']) };
}
function adminLogout(token) { if (token) CacheService.getScriptCache().remove('admsess_' + token); return true; }

/** 網頁版的所有後台動作都經過這裡：先驗通行證，再呼叫白名單內的功能 */
let ADMIN_CTX_ = null;
function adminCall(token, fn, args) {
  const account = token ? CacheService.getScriptCache().get('admsess_' + token) : null;
  if (!account) throw new Error('請先登入後台（登入已過期或尚未登入）');
  const API = {
    adminLoad: adminLoad, adminSaveSettings: adminSaveSettings, adminSaveSkill: adminSaveSkill, adminSkillHistory: adminSkillHistory,
    adminUploadDoc: adminUploadDoc, adminDocChunks: adminDocChunks, adminSetChunkType: adminSetChunkType, adminClassifyDoc: adminClassifyDoc,
    adminSetDocStatus: adminSetDocStatus, adminTestChat: adminTestChat, adminLogs: adminLogs, adminLogDetail: adminLogDetail,
    adminEvalList: adminEvalList, adminAddEval: adminAddEval, adminDeleteEval: adminDeleteEval, adminRunEval: adminRunEval,
    adminRepairVectors: adminRepairVectors, adminFlowStats: adminFlowStats, adminEvalBank: adminEvalBank, adminEvalSetQ: adminEvalSetQ, adminEvalGenPlan: adminEvalGenPlan, adminEvalGenBatch: adminEvalGenBatch, adminEvalGenSpecial: adminEvalGenSpecial, adminEvalImportLogs: adminEvalImportLogs, adminEvalAddExpert: adminEvalAddExpert, adminEvalRunStart: adminEvalRunStart, adminEvalRunOne: adminEvalRunOne, adminEvalRuns: adminEvalRuns, adminEvalSummary: adminEvalSummary, adminEvalReview: adminEvalReview, adminModels: adminModels, adminPingModel: adminPingModel, adminImportKG: adminImportKG, adminKgGenStart: adminKgGenStart, adminKgGenBatch: adminKgGenBatch, adminKgGenFinish: adminKgGenFinish, adminGraphLoad: adminGraphLoad, adminMapDoc: adminMapDoc, adminCandAction: adminCandAction,
  };
  if (!API[fn]) throw new Error('未知的操作');
  ADMIN_CTX_ = account;
  try { return API[fn].apply(null, args || []); } finally { ADMIN_CTX_ = null; }
}

function include(name) { return HtmlService.createHtmlOutputFromFile(name).getContent(); }

/** 回傳目前管理員：網頁登入的帳號，或試算表選單開啟時的 Google 帳號 */
function requireAdmin_() {
  if (ADMIN_CTX_) return ADMIN_CTX_;
  const email = String(Session.getActiveUser().getEmail() || '').toLowerCase();
  if (!email) throw new Error('請先登入後台');
  const ok = rows_('admins').some(r => String(r['Email']).trim().toLowerCase() === email);
  if (!ok) throw new Error('你的帳號（' + email + '）不在「管理員」名單中');
  return email;
}

// ---------- 讀取 ----------
function adminLoad() {
  const me = requireAdmin_();
  const settings = rows_('settings').map(r => ({ key: r['設定'], value: r['值'], note: r['說明'] }));
  const skills = rows_('skills').map(r => ({ id: r['Skill ID'], name: r['名稱'], tags: r['對應主題'], always: truthy_(r['一律載入']), content: r['內容'], enabled: truthy_(r['啟用']), version: r['版本'], by: r['更新者'], at: fmt_(r['更新時間']) }));
  const docs = rows_('docs').map(r => ({ id: r['文件ID'], title: r['標題'], source: r['出處'], evidence: r['證據等級'], file: r['原始檔名'], chunks: r['段落數'], status: r['狀態'], by: r['上傳者'], at: fmt_(r['上傳時間']), reviewer: r['審核者'] })).reverse();
  const topics = Array.from(new Set([].concat.apply([], skills.map(s => String(s.tags || '').split(/[,，、]/).map(x => x.trim()).filter(Boolean)))));
  return { me: me, settings: settings, skills: skills, docs: docs, topics: topics, sheetUrl: ss_().getUrl() };
}
function fmt_(d) { return d instanceof Date ? Utilities.formatDate(d, 'Asia/Taipei', 'yyyy-MM-dd HH:mm') : String(d || ''); }

// ---------- 機器人設定 ----------
function adminSaveSettings(list) {
  requireAdmin_();
  const s = sh_('settings'); const v = s.getDataRange().getValues();
  list.forEach(item => {
    const i = v.findIndex((r, idx) => idx > 0 && r[0] === item.key);
    if (i > 0) s.getRange(i + 1, 2).setValue(safeCell_(item.value));
  });
  clearCfgCache_();
  return true;
}

// ---------- Skill ----------
function adminSaveSkill(sk) {
  const me = requireAdmin_();
  if (!String(sk.name || '').trim()) throw new Error('請填寫 Skill 名稱');
  const s = sh_('skills'); const all = rows_('skills');
  const now = new Date();
  let row = all.find(r => String(r['Skill ID']) === String(sk.id));
  let version = 1, id = sk.id;
  if (row) {
    version = Number(row['版本'] || 1) + 1;
    s.getRange(row._row, 1, 1, 9).setValues(safeRows_([[id, sk.name, sk.tags || '', !!sk.always, sk.content || '', !!sk.enabled, version, me, now]]));
  } else {
    id = 'S' + String(all.length + 1).padStart(2, '0') + '-' + Utilities.getUuid().slice(0, 4);
    append_('skills', [[id, sk.name, sk.tags || '', !!sk.always, sk.content || '', sk.enabled !== false, 1, me, now]]);
  }
  append_('skillHist', [[id, version, sk.name, sk.tags || '', !!sk.always, sk.content || '', me, now]]);
  return { id: id, version: version };
}
function adminSkillHistory(id) {
  requireAdmin_();
  return rows_('skillHist').filter(r => String(r['Skill ID']) === String(id)).map(r => ({ version: r['版本'], name: r['名稱'], tags: r['對應主題'], always: truthy_(r['一律載入']), content: r['內容'], by: r['更新者'], at: fmt_(r['更新時間']) })).reverse();
}

// ---------- 知識庫文件 ----------
function adminUploadDoc(p) {
  const me = requireAdmin_();
  const text = p.text ? String(p.text) : extractText_(p.fileName, p.mimeType, p.base64);
  return addDocument_({ title: p.title, source: p.source, evidence: p.evidence, fileName: p.fileName || '（貼上文字）' }, text, me);
}
function adminDocChunks(docId) {
  requireAdmin_();
  const nodes = {}; const label = {};
  if (kgHasGraph_()) {
    kgNodes_(true).forEach(n => label[String(n['節點ID'])] = String(n['名稱']));
    rows_('kgLinks').forEach(l => { const k = String(l['段落ID']); const tag = String(l['標籤'] || '主要');
      (nodes[k] = nodes[k] || []).push((tag === '主要' ? '' : tag + '：') + (label[String(l['節點ID'])] || String(l['節點ID']))); });
  }
  return rows_('chunks').filter(r => String(r['文件ID']) === String(docId)).map(r => ({ id: r['段落ID'], n: r['序號'], page: r['頁碼'], text: r['內容'], status: r['狀態'], type: String(r['類型'] || ''),
    nodes: Array.from(new Set(nodes[String(r['段落ID'])] || [])).sort((a, b) => (a.indexOf('：') >= 0) - (b.indexOf('：') >= 0)),
    meta: ['讀者對象', '適用年齡', '情境'].map(h => r[h] ? h + '：' + r[h] : '').filter(Boolean).join('｜') }));
}
/** 修改單一段落的類型 */
function adminSetChunkType(chunkId, type) {
  requireAdmin_();
  if (type && CHUNK_TYPES.indexOf(type) < 0) throw new Error('類型不正確');
  const s = sh_('chunks'); const head = s.getRange(1, 1, 1, s.getLastColumn()).getValues()[0];
  const ti = head.indexOf('類型'); if (ti < 0) throw new Error('「知識段落」缺少「類型」欄，請先執行 setup()');
  const r = rows_('chunks').find(x => String(x['段落ID']) === String(chunkId)); if (!r) throw new Error('找不到段落');
  s.getRange(r._row, ti + 1).setValue(type || '');
  return true;
}
/** 對一份文件中還沒有類型的段落做 AI 分類（舊文件補分類用） */
function adminClassifyDoc(docId) {
  requireAdmin_();
  const s = sh_('chunks'); const head = s.getRange(1, 1, 1, s.getLastColumn()).getValues()[0];
  const ti = head.indexOf('類型'); if (ti < 0) throw new Error('「知識段落」缺少「類型」欄，請先執行 setup()');
  const todo = rows_('chunks').filter(r => String(r['文件ID']) === String(docId) && !String(r['類型'] || ''));
  const types = classifyChunks_(todo.map(r => String(r['內容'])));
  todo.forEach((r, i) => { if (types[i]) s.getRange(r._row, ti + 1).setValue(types[i]); });
  return { total: todo.length, done: types.filter(Boolean).length };
}
function adminSetDocStatus(docId, status) {
  const me = requireAdmin_();
  if (['已核可', '待審核', '停用'].indexOf(status) < 0) throw new Error('狀態不正確');
  setDocStatus_(docId, status, me);
  return true;
}

// ---------- 測試對話（不需邀請碼，回傳完整流程紀錄） ----------
function adminTestChat(req) {
  requireAdmin_();
  req.session = req.session || 'admin-test'; req.school = '後台測試';
  return runChat_(req, { trace: true });
}

// ---------- 對話紀錄觀測 ----------
const LOG_TYPE_NAME = { answer: '回答', ai_fallback: 'AI 補足', continue: '延續', followup: '追問', referral: '緊急轉介', conservative: '保守回答', out_of_scope: '超出範圍' };
function jsonOr_(s, d) { try { return JSON.parse(s); } catch (e) { return d; } }
function feedbackMap_() {
  const fb = {};
  rows_('feedback').forEach(r => {
    const k = r['Session'] + '|' + r['輪次'];
    (fb[k] = fb[k] || []).push(String(r['類型']));
  });
  return fb;
}

/**
 * 讀對話紀錄並計算指標。opts：days（0 = 全部）、school、type、q（關鍵字）、includeTest
 * 命中率的定義：
 *  - 檢索命中率：走到檢索的回合中，至少有 1 段超過門檻的比例
 *  - 引用率：檢索到的段落中，被回答實際引用的比例（越高代表檢索越準）
 *  - 平均最高相似度：每回合第一名段落的分數平均
 */
function adminLogs(opts) {
  requireAdmin_();
  opts = opts || {};
  const since = opts.days ? Date.now() - opts.days * 86400000 : 0;
  const fb = feedbackMap_();
  const all = rows_('logs').filter(r => {
    const t = r['時間'] instanceof Date ? r['時間'].getTime() : 0;
    if (since && t < since) return false;
    if (!opts.includeTest && String(r['Session']).indexOf('admin-test') === 0) return false;
    if (opts.school && String(r['園所']) !== opts.school) return false;
    if (opts.type && String(r['回應類型']) !== opts.type) return false;
    if (opts.q && String(r['老師訊息（去識別化）']).indexOf(opts.q) < 0) return false;
    return true;
  });
  const num = v => v === '' || v == null ? null : Number(v);
  const rows = all.map(r => {
    const f = fb[r['Session'] + '|' + r['輪次']] || [];
    return {
      row: r._row, time: fmt_(r['時間']), session: String(r['Session']), school: String(r['園所']), turn: r['輪次'],
      msg: String(r['老師訊息（去識別化）']).slice(0, 120), type: String(r['回應類型']), referral: String(r['轉介'] || ''),
      retrieved: num(r['檢索段數']), top: num(r['最高相似度']), cited: num(r['被引用段數']), skills: String(r['載入Skill'] || ''),
      rewritten: r['是否重寫'] === '是', ms: num(r['耗時ms']),
      fb: f.indexOf('有幫助') >= 0 ? 'up' : f.indexOf('沒幫助') >= 0 ? 'down' : '', copied: f.indexOf('複製家長訊息') >= 0,
    };
  });
  const ret = rows.filter(r => r.retrieved != null);
  const sum = (a, f) => a.reduce((s, x) => s + f(x), 0);
  const pct = (a, b) => b ? Math.round(a / b * 1000) / 10 : null;
  const byType = {}; rows.forEach(r => byType[r.type] = (byType[r.type] || 0) + 1);
  const ups = rows.filter(r => r.fb === 'up').length, downs = rows.filter(r => r.fb === 'down').length;
  const answered = rows.filter(r => r.type === 'answer' || r.type === 'continue' || r.type === 'ai_fallback');
  const stats = {
    turns: rows.length,
    sessions: new Set(rows.map(r => r.session)).size,
    byType: byType,
    retrievalTurns: ret.length,
    hitRate: pct(ret.filter(r => r.retrieved > 0).length, ret.length),
    citeRate: pct(sum(answered, r => r.cited || 0), sum(answered, r => r.retrieved || 0)),
    avgTop: ret.length ? Math.round(sum(ret, r => r.top || 0) / ret.length * 1000) / 1000 : null,
    rewriteRate: pct(answered.filter(r => r.rewritten).length, answered.length),
    upRate: pct(ups, ups + downs), ups: ups, downs: downs,
    copied: rows.filter(r => r.copied).length,
    avgMs: rows.length ? Math.round(sum(rows, r => r.ms || 0) / rows.length) : null,
  };
  // 知識缺口：走到檢索卻沒有命中，或最後只能保守回答的問題 → 交給專家補資料
  const gaps = rows.filter(r => r.retrieved === 0 || r.type === 'conservative' || r.type === 'ai_fallback').slice(-30).reverse()
    .map(r => ({ row: r.row, time: r.time, msg: r.msg, top: r.top }));
  // 常被引用的文件
  const docCount = {};
  all.forEach(r => String(r['引用'] || '').split('｜').filter(Boolean).forEach(x => { const t = x.replace(/^K\d+:/, ''); docCount[t] = (docCount[t] || 0) + 1; }));
  const topDocs = Object.keys(docCount).map(k => ({ title: k, n: docCount[k] })).sort((a, b) => b.n - a.n).slice(0, 10);
  const schools = Array.from(new Set(rows_('logs').map(r => String(r['園所'])).filter(Boolean)));
  return { stats: stats, stability: stabilityStats_(all), quality: qualityStats_(all, rows), rows: rows.slice(-300).reverse(), gaps: gaps, topDocs: topDocs, schools: schools, typeNames: LOG_TYPE_NAME };
}

/** 品質問題統計：知識不足（交給專家補文件）與 L5 發現的問題（調整 Skill／提示詞） */
function qualityStats_(all, rows) {
  const typeOf = {}; rows.forEach(r => typeOf[r.row] = r);
  const out = { turns: 0, retrieval: 0,
    gap: { n: 0, byKind: {}, byOutcome: {}, byTopic: {}, list: [] },
    l5: { checked: 0, any: 0, severe: 0, minorOnly: 0, byType: {}, severeByType: {}, outcome: {}, list: [] } };
  all.forEach(r => {
    let q = null; try { q = r['品質問題'] ? JSON.parse(r['品質問題']) : null; } catch (e) {}
    if (!q) return;
    out.turns++;
    const base = typeOf[r._row] || {};
    if (r['檢索段數'] !== '' && r['檢索段數'] != null) out.retrieval++;
    const msg = String(r['老師訊息（去識別化）']).slice(0, 160), time = fmt_(r['時間']);
    if (q.gap) {
      const g = out.gap; g.n++;
      g.byKind[q.gap.kind] = (g.byKind[q.gap.kind] || 0) + 1;
      g.byOutcome[q.gap.outcome] = (g.byOutcome[q.gap.outcome] || 0) + 1;
      const tps = (q.gap.topics || []).length ? q.gap.topics : ['（沒有對應主題）'];
      tps.forEach(t => g.byTopic[t] = (g.byTopic[t] || 0) + 1);
      g.list.push({ row: r._row, time: time, msg: msg, focus: String(q.gap.focus || ''), kind: q.gap.kind, outcome: q.gap.outcome, topics: tps.join('、'), top: q.gap.top, school: base.school || '' });
    }
    const type = String(r['回應類型']);
    if (['answer', 'continue', 'ai_fallback', 'conservative'].indexOf(type) >= 0 && !(q.gap && q.gap.outcome === '保守回答')) out.l5.checked++;
    const rounds = q.l5 || [];
    if (rounds.length) {
      const L = out.l5; L.any++;
      const all5 = [].concat.apply([], rounds.map(x => (x.issues || []).map(i => Object.assign({ attempt: x.attempt }, i))
        .concat(x.relevance ? [{ attempt: x.attempt, type: '回應度', severe: true, note: x.relevance.note || ('沒有回應到「' + (x.relevance.focus || '') + '」') }] : [])));
      const hasSevere = all5.some(i => i.severe);
      if (hasSevere) L.severe++; else L.minorOnly++;
      all5.forEach(i => { L.byType[i.type] = (L.byType[i.type] || 0) + 1; if (i.severe) L.severeByType[i.type] = (L.severeByType[i.type] || 0) + 1; });
      const oc = q.l5Final === '保守回答' ? '重寫後仍未通過 → 保守回答' : !hasSevere ? '只有小問題（不擋）' : rounds.length > 1 || String(r['是否重寫']) === '是' ? '重寫後通過' : '其他';
      L.outcome[oc] = (L.outcome[oc] || 0) + 1;
      L.list.push({ row: r._row, time: time, msg: msg, outcome: oc, items: all5.slice(0, 6) });
    }
  });
  out.gap.list = out.gap.list.slice(-200).reverse();
  out.l5.list = out.l5.list.slice(-100).reverse();
  return out;
}

/** 穩定度統計：哪一步、哪個模型、出現什麼格式問題；重寫是因為什麼 */
function stabilityStats_(rows) {
  const out = { turns: 0, withIssue: 0, byKind: {}, byStep: {}, byModel: {}, rewriteWhy: {}, recent: [] };
  rows.forEach(r => {
    let st = null; try { st = r['穩定度'] ? JSON.parse(r['穩定度']) : null; } catch (e) {}
    if (!st || !st.models) return;
    out.turns++;
    const ev = st.ev || [];
    const model = st.models.answer || '';
    const m = out.byModel[model] || (out.byModel[model] = { turns: 0, withIssue: 0, a1: 0 });
    m.turns++;
    if (ev.length) { out.withIssue++; m.withIssue++; }
    if (ev.some(e => e.at === 'A1')) m.a1++;
    ev.forEach(e => {
      out.byKind[e.kind] = (out.byKind[e.kind] || 0) + 1;
      const k = (e.at || '其他') + '｜' + e.kind; out.byStep[k] = (out.byStep[k] || 0) + 1;
    });
    (st.rewriteWhy || []).forEach(w => out.rewriteWhy[w] = (out.rewriteWhy[w] || 0) + 1);
    if (ev.length && out.recent.length < 200) out.recent.push({ row: r._row, time: fmt_(r['時間']), ev: ev.slice(0, 5) });
  });
  out.recent = out.recent.slice(-20).reverse();
  return out;
}

/** 單一回合明細：同一個 Session 的完整對話 + 這一回合的流程紀錄 */
function adminLogDetail(row) {
  requireAdmin_();
  const s = sh_('logs');
  const head = s.getRange(1, 1, 1, s.getLastColumn()).getValues()[0];
  const v = s.getRange(row, 1, 1, head.length).getValues()[0];
  const o = {}; head.forEach((h, i) => o[h] = v[i]);
  const fb = feedbackMap_();
  const session = String(o['Session']);
  const turns = rows_('logs').filter(r => String(r['Session']) === session).map(r => ({
    row: r._row, turn: r['輪次'], time: fmt_(r['時間']), msg: String(r['老師訊息（去識別化）']), type: String(r['回應類型']),
    reply: jsonOr_(r['回答'], r['回答']), fb: (fb[session + '|' + r['輪次']] || []).join('、'),
  }));
  return {
    row: row, time: fmt_(o['時間']), session: session, school: String(o['園所']), turn: o['輪次'], type: String(o['回應類型']),
    msg: String(o['老師訊息（去識別化）']), triage: jsonOr_(o['分流'], {}), reply: jsonOr_(o['回答'], o['回答']),
    sources: String(o['引用'] || ''), checks: jsonOr_(o['檢查'], []), rewritten: o['是否重寫'] === '是', ms: o['耗時ms'],
    metrics: { retrieved: o['檢索段數'] === '' ? null : o['檢索段數'], top: o['最高相似度'] === '' ? null : o['最高相似度'], cited: o['被引用段數'] === '' ? null : o['被引用段數'], skills: String(o['載入Skill'] || '').split('、').filter(Boolean) },
    trace: jsonOr_(o['流程紀錄'], []), turns: turns,
  };
}

// ---------- 命中率測試（P17 回歸測試的檢索部分） ----------
/**
 * 「檢索測試」工作表：每列一個使用者可能會問的問題 + 專家認為應該找到的文件 ID（可多個，逗號分隔）。
 * 對每題做一次檢索，看應命中的文件有沒有出現在最後採用的段落中：
 *  - Hit@K：有出現的題目比例
 *  - MRR：應命中文件第一次出現的名次倒數的平均（1 = 每題都排第一）
 */
function adminEvalList() {
  requireAdmin_();
  return rows_('evalset').map(r => ({ row: r._row, q: String(r['測試問題']), expect: String(r['應命中文件ID']), note: String(r['備註'] || ''), result: String(r['結果'] || ''), rank: r['名次'], top: r['最高分'], hit: String(r['命中段落'] || ''), at: fmt_(r['測試時間']) }));
}
function adminAddEval(q, expect, note) {
  requireAdmin_();
  if (!String(q || '').trim() || !String(expect || '').trim()) throw new Error('請填寫測試問題與應命中文件');
  append_('evalset', [[String(q).trim(), String(expect).trim(), note || '', '', '', '', '', '']]);
  return adminEvalList();
}
function adminDeleteEval(row) {
  requireAdmin_();
  sh_('evalset').deleteRow(row);
  return adminEvalList();
}
function adminRunEval() {
  requireAdmin_();
  const list = rows_('evalset').filter(r => String(r['測試問題']).trim());
  if (!list.length) throw new Error('「檢索測試」還沒有題目');
  const exps = list.map(r => expandQuery_(String(r['測試問題'])));
  const vecs = embedTexts_(exps.map(e => e.text), 'RETRIEVAL_QUERY');
  const s = sh_('evalset'); const now = new Date();
  let hitN = 0, rr = 0, topSum = 0;
  const results = list.map((r, i) => {
    const expect = String(r['應命中文件ID']).split(/[,，、\s]+/).filter(Boolean);
    const sd = searchDetail_(vecs[i], String(r['測試問題']), exps[i].concepts);
    const candRank = sd.cands.findIndex(c => expect.indexOf(c.docId) >= 0) + 1;     // 前 10 名中的名次（不論門檻）
    const hit = sd.hits.find(h => expect.indexOf(h.docId) >= 0);
    const hitRank = hit ? sd.hits.indexOf(hit) + 1 : 0;
    if (hit) { hitN++; rr += 1 / hitRank; }
    const top = sd.cands.length ? sd.cands[0].score : 0; topSum += top;
    const result = hit ? '命中' : candRank ? '未達門檻' : '沒找到';
    const hitTxt = hit ? hit.title + (hit.page ? ' p.' + hit.page : '') + '（' + hit.score + '）' : candRank ? '第 ' + candRank + ' 名但分數 ' + sd.cands[candRank - 1].score + ' 低於門檻 ' + sd.minScore : '';
    s.getRange(r._row, 4, 1, 5).setValues(safeRows_([[result, hitRank || (candRank ? '>' + sd.topK : ''), top, hitTxt, now]]));
    return { row: r._row, q: String(r['測試問題']), expect: expect.join(', '), result: result, rank: hitRank, candRank: candRank, top: top, hit: hitTxt,
      cands: sd.cands.slice(0, 5).map(c => ({ docId: c.docId, title: c.title, page: c.page, score: c.score, pass: c.pass, ok: expect.indexOf(c.docId) >= 0 })) };
  });
  const n = list.length;
  return { n: n, hitRate: Math.round(hitN / n * 1000) / 10, mrr: Math.round(rr / n * 1000) / 1000, avgTop: Math.round(topSum / n * 1000) / 1000,
    minScore: Number(cfg_('min_score') || 0.45), topK: Number(cfg_('top_k') || 6), results: results };
}

// ---------- 模型 ----------
/** 文字生成用不到的模型（語音、圖片、即時、音樂、機器人…） */
const MODEL_SKIP = /tts|image|live|audio|transcribe|translate|lyria|imagen|veo|robotics|banana|aqa|omni|deep-research|antigravity|computer-use/i;
/** OpenAI 清單只有 ID：用名稱判斷哪些能拿來產生文字 */
const OA_SKIP = /audio|realtime|tts|transcribe|whisper|image|dall-e|sora|search|moderation|embedding|babbage|davinci|instruct|codex|computer-use|chat-latest|deep-research/i;
function adminModels(force) {
  requireAdmin_();
  const ver = m => modelVer_(m.id);
  const sort = a => a.sort((x, y) => ver(y) - ver(x) || (/preview|exp/.test(x.id) - /preview|exp/.test(y.id)) || x.id.localeCompare(y.id));
  const out = { total: 0, gen: [], embed: [], v3: 0, oa: 0, oaTotal: 0, errors: [] };
  try {
    const all = listModels_(!!force);
    out.total = all.length;
    out.gen = sort(all.filter(m => m.methods.indexOf('generateContent') >= 0 && !MODEL_SKIP.test(m.id)));
    out.embed = sort(all.filter(m => m.methods.indexOf('embedContent') >= 0 || m.methods.indexOf('batchEmbedContents') >= 0));
    out.v3 = out.gen.filter(m => ver(m) >= 3).length;
  } catch (e) { out.errors.push('Gemini：' + e.message); }
  try {
    const oa = listOpenAIModels_(!!force);
    const oaVer = id => { const m = id.match(/^gpt-(\d+(?:\.\d+)?)/); return m ? Number(m[1]) : 0; };
    // 有日期後綴的快照（例如 gpt-5-2025-08-07）不列，避免清單太長
    const gen = oa.filter(m => /^(gpt-|o\d)/.test(m.id) && !OA_SKIP.test(m.id) && !/-\d{4}-\d{2}-\d{2}$/.test(m.id))
      .sort((a, b) => oaVer(b.id) - oaVer(a.id) || a.id.localeCompare(b.id));
    out.oaTotal = oa.length; out.oa = gen.length;
    out.gen = out.gen.concat(gen);
    out.embed = out.embed.concat(oa.filter(m => /^text-embedding-3/.test(m.id)));
  } catch (e) { out.errors.push('OpenAI：' + e.message); }
  if (!out.gen.length && out.errors.length) throw new Error(out.errors.join('；'));
  return out;
}
/** 用一句話試跑模型，確認這把金鑰真的能用、速度多快 */
function adminPingModel(model, kind) {
  requireAdmin_();
  const t = Date.now();
  if (kind === 'embed' && isOpenAI_(model)) {
    const v = embedParse_(UrlFetchApp.fetch(OPENAI_BASE + 'embeddings', { method: 'post', contentType: 'application/json', muteHttpExceptions: true,
      headers: { Authorization: 'Bearer ' + openaiKey_() }, payload: JSON.stringify({ model: model, input: ['測試'], dimensions: Number(cfg_('embed_dim') || 768) }) }))[0];
    return { ok: true, ms: Date.now() - t, reply: '向量維度 ' + v.length };
  }
  if (kind === 'embed') {
    const res = UrlFetchApp.fetch(GEMINI_BASE + model + ':embedContent', { method: 'post', contentType: 'application/json', muteHttpExceptions: true, headers: { 'x-goog-api-key': apiKey_() },
      payload: JSON.stringify({ model: 'models/' + model, content: { parts: [{ text: '測試' }] }, outputDimensionality: Number(cfg_('embed_dim') || 768) }) });
    if (res.getResponseCode() !== 200) throw new Error(res.getResponseCode() + ': ' + res.getContentText().slice(0, 200));
    const d = JSON.parse(res.getContentText());
    return { ok: true, ms: Date.now() - t, reply: '向量維度 ' + (((d.embedding || {}).values || []).length) };
  }
  const r = genJson_(model, '只輸出 JSON：{"ok":true,"say":"用一句繁體中文打招呼"}', { temperature: 0, noThink: true });
  return { ok: true, ms: Date.now() - t, reply: String(r.say || JSON.stringify(r)).slice(0, 60) };
}

function adminRepairVectors() { requireAdmin_(); return repairVectors_(); }

// ---------- 流程控制台：依對話紀錄算出每一步的運作數據 ----------
const FLOW_STEP_OF = [['P03', 'deid'], ['L4', 'triage'], ['P04', 'state'], ['P05', 'skill'], ['P06', 'retrieve'], ['重新排序', 'rerank'], ['P07', 'assemble'], ['A1', 'answer'], ['檢查', 'check'], ['P10', 'final']];
function flowStepKey_(step) { const s = String(step || ''); const m = FLOW_STEP_OF.find(x => s.indexOf(x[0]) === 0); return m ? m[1] : ''; }
function adminFlowStats(opts) {
  requireAdmin_(); opts = opts || {};
  const since = opts.days ? Date.now() - opts.days * 86400000 : 0;
  let rs = rows_('logs').filter(r => {
    const t = r['時間'] instanceof Date ? r['時間'].getTime() : 0;
    if (since && t < since) return false;
    if (!opts.includeTest && String(r['Session']).indexOf('admin-test') === 0) return false;
    return true;
  });
  rs = rs.slice(-500);   // 最多看最近 500 回合，避免讀太久
  const st = {}; const node = k => st[k] || (st[k] = { runs: 0, ms: 0 });
  const out = { turns: rs.length, ms: 0, types: {}, retrieval: { n: 0, hit: 0, top: 0, topN: 0 }, rerank: { n: 0, used: 0, failed: 0, off: 0 },
    gap: { n: 0, byKind: {}, byOutcome: {} }, answer: { n: 0, rewrite: 0, format: 0 }, check: { n: 0, l5: 0, severe: 0, conservative: 0 }, steps: st };
  rs.forEach(r => {
    const type = String(r['回應類型']); out.types[type] = (out.types[type] || 0) + 1;
    out.ms += Number(r['耗時ms'] || 0);
    const tr = jsonOr_(r['流程紀錄'], []); let prev = 0; const seen = {};
    (Array.isArray(tr) ? tr : []).forEach(t => {
      const k = flowStepKey_(t.step); const ms = Number(t.ms || 0); const d = Math.max(0, ms - prev); prev = ms;
      if (!k) return; const o = node(k); o.ms += d; if (!seen[k]) { o.runs++; seen[k] = 1; }
      if (k === 'rerank' && t.detail && typeof t.detail === 'object') {
        out.rerank.n++; if (t.detail.used) out.rerank.used++; else if (/失敗/.test(t.detail.why || '')) out.rerank.failed++; else if (/關閉/.test(t.detail.why || '')) out.rerank.off++;
      }
    });
    if (r['檢索段數'] !== '' && r['檢索段數'] != null) {
      out.retrieval.n++; if (Number(r['檢索段數']) > 0) out.retrieval.hit++;
      if (r['最高相似度'] !== '' && r['最高相似度'] != null) { out.retrieval.top += Number(r['最高相似度']); out.retrieval.topN++; }
    }
    const q = jsonOr_(r['品質問題'], null) || {};
    if (q.gap) { out.gap.n++; out.gap.byKind[q.gap.kind] = (out.gap.byKind[q.gap.kind] || 0) + 1; out.gap.byOutcome[q.gap.outcome] = (out.gap.byOutcome[q.gap.outcome] || 0) + 1; }
    if (seen.answer) {
      out.answer.n++; if (String(r['是否重寫']) === '是') out.answer.rewrite++;
      const s = jsonOr_(r['穩定度'], null) || {}; if ((s.ev || []).some(e => e.at === 'A1')) out.answer.format++;
    }
    if (seen.check) {
      out.check.n++;
      const l5 = q.l5 || []; if (l5.length) out.check.l5++;
      if (l5.some(x => (x.issues || []).some(i => i.severe) || x.relevance)) out.check.severe++;
      if (q.l5Final === '保守回答') out.check.conservative++;
    }
  });
  Object.keys(st).forEach(k => st[k].avg = st[k].runs ? Math.round(st[k].ms / st[k].runs) : null);
  out.avgMs = rs.length ? Math.round(out.ms / rs.length) : null;
  out.skills = rows_('skills').filter(r => truthy_(r['啟用'])).length;
  out.skillsAlways = rows_('skills').filter(r => truthy_(r['啟用']) && truthy_(r['一律載入'])).length;
  out.chunks = rows_('chunks').filter(r => String(r['狀態']) === '已核可').length;
  out.synonyms = Math.max(0, sh_('synonyms').getLastRow() - 1);
  out.forbidden = Math.max(0, sh_('forbidden').getLastRow() - 1);
  out.referral = Math.max(0, sh_('referral').getLastRow() - 1);
  return out;
}
