/**
 * 知識圖譜：當架構用（內容靠上傳的文件）
 *  - 匯入：後台上傳 knowledge-graph.json → 節點、關係、來源、圖譜定義（節點類型與關係的定義）
 *  - 上傳文件時：照 ontology 的「文章標註」規格標每一段
 *      主要標籤＝這段直接討論的節點；次要標籤＝這段隱含的理論、策略或解釋
 *      讀者對象、適用年齡、情境：段落有明講才標，沒標＝不限
 *      現有節點不夠時提出「新節點候選」（ontology 的 proposedNode）
 *  - 專家在後台審核候選：核可（新增節點）、合併到既有節點、略過
 *  - 關係檢查：ontology 沒有限制哪種節點能接哪種關係，所以用圖譜現有關係歸納出常見接法，少見的接法只提醒、不擋
 *  - 覆蓋度：每個節點有幾段文件以它為主要標籤、幾段已核可、幾段是具體做法
 * 目前回答流程不讀圖譜（之後第 3 步才接）。
 */

const KG_FROM_IMPORT = '知識圖譜匯入', KG_FROM_PROPOSAL = '文件提案';
const KG_TAG_MAIN = '主要', KG_TAG_SUB = '次要';
const KG_AUDIENCES = ['家長', '教保服務人員', '專業人員'];
// 匯入前的預設值（與 knowledge-graph.json 1.0.0 相同）；匯入後以「圖譜定義」表為準
const KG_DEFS_DEFAULT = {
  types: [['hub', '主軸', '知識圖譜的五大主軸。'], ['sub', '子分類', '主軸底下的主題分類。'], ['policy', '法規課綱', '法律、行政規則、課程大綱及其構成要素。'],
    ['approach', '課程取向', '幼兒園課程設計與教學模式。'], ['concept', '概念', '發展、心理或教育上的概念與現象。'],
    ['practice', '實務策略', '教保服務人員或專業人員可採取的做法、工具或服務。'], ['theory', '理論', '心理學或教育學理論、模型與經典研究。'],
    ['category', '障礙與需求類別', '特殊教育鑑定類別與學前常見的特殊需求。']],
  rels: [['hasPart', '包含', '上層分類包含下層項目（樹狀階層）。'], ['regulates', '規範', '法規或制度對某項實務、對象或程序的規範、授權或保障。'],
    ['theoreticalBasisFor', '理論基礎', '理論、研究或概念為某項實務或概念提供理論依據。'], ['appliedIn', '應用於', '概念、能力或策略在某情境、區域或作息中被應用或練習。'],
    ['hasStrategy', '可用策略', '某項需求、行為或目標可搭配的策略或調整方式。'], ['supports', '支持', '專業人員、資源或環境對某對象或能力的支持、評估或服務。'],
    ['correspondsTo', '對應', '兩者在內容上相互對應、屬於或呼應。'], ['influences', '影響', '一方會促進、阻礙、預測或觸發另一方。'],
    ['precedes', '後續為', '發展階段、流程步驟或演變上的先後關係。'], ['informs', '提供資訊', '評量、觀察或資料為另一項工作提供依據或材料。'],
    ['relatedTo', '相關', '無法歸入上述類型的一般關聯。']],
};
const KG_TOP_TYPE = '主軸'; // 主軸只有圖譜本身的五個，不接受新增

function kgHasGraph_() { try { return sh_('kgNodes').getLastRow() > 1; } catch (e) { return false; } }
function kgNodes_(includeOff) { return rows_('kgNodes').filter(r => includeOff || String(r['狀態'] || '啟用') !== '停用'); }

/** 節點類型與關係的定義：{types:[{id,label,def}], rels:[{id,label,def}]} */
function kgDefs_() {
  let rs = [];
  try { rs = rows_('kgDefs'); } catch (e) {}
  const pick = (kind, dflt) => {
    const a = rs.filter(r => r['種類'] === kind).map(r => ({ id: String(r['ID']), label: String(r['名稱']), def: String(r['定義'] || '') }));
    return a.length ? a : dflt.map(x => ({ id: x[0], label: x[1], def: x[2] }));
  };
  return { types: pick('節點類型', KG_DEFS_DEFAULT.types), rels: pick('關係', KG_DEFS_DEFAULT.rels) };
}
function kgRelIds_(defs) { return defs.rels.map(r => r.id); }
function kgRelLabel_(defs, id) { const r = defs.rels.find(x => x.id === id); return r ? r.label : id; }
function kgNewTypes_(defs) { return defs.types.map(t => t.label).filter(l => l !== KG_TOP_TYPE); }

/** 整張表重寫：保留 keep 的列，加上 add 的列 */
function kgRewrite_(key, keep, add) {
  const s = sh_(key), width = s.getLastColumn();
  if (s.getLastRow() > 1) s.getRange(2, 1, s.getLastRow() - 1, width).clearContent();
  const all = keep.concat(add);
  if (all.length) s.getRange(2, 1, all.length, width).setValues(safeRows_(all.map(r => r.concat(Array(Math.max(0, width - r.length)).fill('')).slice(0, width))));
}
function kgRowArr_(key, r) {
  const s = sh_(key); const head = s.getRange(1, 1, 1, s.getLastColumn()).getValues()[0];
  return head.map(h => r[h] == null ? '' : r[h]);
}
function kgHead_(key) { const s = sh_(key); return s.getRange(1, 1, 1, s.getLastColumn()).getValues()[0]; }

// ---------- 匯入 ----------
/** 匯入 knowledge-graph.json；只取代「知識圖譜匯入」來的節點與關係，文件提案新增的節點保留 */
function adminImportKG(jsonText) {
  const me = requireAdmin_();
  let d; try { d = JSON.parse(String(jsonText || '').replace(/^﻿/, '')); } catch (e) { throw new Error('檔案不是有效的 JSON'); }
  if (!Array.isArray(d.nodes) || !Array.isArray(d.edges)) throw new Error('找不到 nodes 或 edges，請上傳 knowledge-graph.json');
  const ver = KG_FROM_IMPORT + ' v' + ((d.meta && d.meta.version) || '');
  const now = new Date();
  // 圖譜定義：節點類型與關係（含定義），給 AI 標註時參考
  const defRows = [];
  (d.nodeTypes || []).forEach(t => t && t.label && defRows.push(['節點類型', t.id || '', t.label, t.definition || '']));
  (d.relations || []).forEach(r => r && r.id && defRows.push(['關係', r.id, r.label || r.id, r.definition || '']));
  if (defRows.length) kgRewrite_('kgDefs', [], defRows);
  const old = {}; kgNodes_(true).forEach(r => old[String(r['節點ID'])] = r);
  const keepNodes = kgNodes_(true).filter(r => String(r['來源']).indexOf(KG_FROM_IMPORT) !== 0).map(r => kgRowArr_('kgNodes', r));
  const addNodes = d.nodes.filter(n => n && n.id).map(n => {
    const o = old[String(n.id)] || {};
    return [String(n.id), n.label || '', n.typeLabel || n.type || '', n.parent || '', [].concat(n.path || []).join(' > '), n.definition || '',
      [].concat(n.practiceTips || []).join(' | '), [].concat(n.sources || []).join(';'), o['同義說法'] || '', o['狀態'] || '啟用', ver, now];
  });
  const ids = {}; addNodes.forEach(r => ids[r[0]] = true); keepNodes.forEach(r => ids[r[0]] = true);
  kgRewrite_('kgNodes', keepNodes, addNodes);
  const keepEdges = rows_('kgEdges').filter(r => String(r['來源']).indexOf(KG_FROM_IMPORT) !== 0).map(r => kgRowArr_('kgEdges', r));
  const addEdges = d.edges.filter(e => e && ids[e.source] && ids[e.target]).map(e => [String(e.source), e.relation || 'relatedTo', String(e.target), e.label || '', ver]);
  kgRewrite_('kgEdges', keepEdges, addEdges);
  const addSrc = (d.sources || []).filter(s => s && s.id).map(s => [String(s.id), s.title || '', s.publisher || '', s.url || '', s.scope || '']);
  kgRewrite_('kgSources', [], addSrc);
  return { nodes: addNodes.length, kept: keepNodes.length, edges: addEdges.length, sources: addSrc.length, defs: defRows.length, by: me };
}

// ---------- 關係接法：從現有關係歸納（ontology 本身沒有限制） ----------
/** 回傳 {relId: {'來源類型>目標類型': 次數}} */
function kgPairs_(nodes, edges) {
  const type = {}; nodes.forEach(r => type[String(r['節點ID'])] = String(r['類型']));
  const p = {};
  edges.forEach(e => {
    const a = type[String(e['主詞'])], b = type[String(e['受詞'])]; if (!a || !b) return;
    const m = p[e['關係']] || (p[e['關係']] = {}); const k = a + '>' + b; m[k] = (m[k] || 0) + 1;
  });
  return p;
}
/** 少見接法的提醒文字；沒問題回傳空字串 */
function kgRelWarn_(pairs, defs, rel, srcType, tgtType) {
  if (rel === 'relatedTo') return ''; // 「相關」是一般關聯，不檢查
  const m = pairs[rel] || {}; const n = m[srcType + '>' + tgtType] || 0;
  if (n > 0) return '';
  const common = Object.keys(m).sort((a, b) => m[b] - m[a]).slice(0, 3).map(k => k.replace('>', '→')).join('、');
  return '圖譜裡沒有「' + srcType + ' →' + kgRelLabel_(defs, rel) + '→ ' + tgtType + '」這種接法' + (common ? '（常見：' + common + '）' : '') + '，請確認';
}

// ---------- 上傳文件時：照 ontology 的文章標註規格標段落 ----------
function kgNodeList_() {
  return kgNodes_().map(r => String(r['節點ID']) + '｜' + r['名稱'] + (r['同義說法'] ? '（' + r['同義說法'] + '）' : '') + '｜' + r['類型'] + '｜' + String(r['路徑'] || '').split(' > ').slice(-1)[0]).join('\n');
}

function kgMapPrompt_(defs, list, title, group) {
  const types = kgNewTypes_(defs);
  return '你在替幼教知識文件做標註，只輸出 JSON。\n\n' +
    '【標註規格】\n' +
    '- main（主要標籤）：這段「直接討論」的節點，1–3 個。\n' +
    '- sub（次要標籤）：這段沒有直接點名、但隱含運用的理論、策略或解釋，0–3 個。只是順帶提到的不要標。\n' +
    '- 標籤只能用節點清單裡的 ID。\n' +
    '- audience（讀者對象）：這段是寫給誰看的，從 ' + JSON.stringify(KG_AUDIENCES) + ' 選，可多選；看不出來就給空陣列。\n' +
    '- age（適用年齡）：段落「明講」的年齡範圍，例如「3–4歲」；沒明講就給空字串（代表不限年齡，不要自己推測）。\n' +
    '- context（情境）：段落明講的發生情境，例如「入園適應」「轉銜」「用餐」；沒有就給空陣列。\n' +
    '- new（建議新增節點）：只有當這段的主要內容在清單裡找不到合適節點時才提出。欄位：name（簡短名稱）、definition（一句話）、' +
    'parent（最適合的上層節點 ID）、type（節點類型名稱）、relation（與某個既有節點的關係，可省略：{"type":關係ID,"target":節點ID}，方向為「新節點 → 目標」）、reason（為什麼現有節點不夠）。\n\n' +
    '【節點類型】（新節點的 type 只能從這些選）\n' + defs.types.filter(t => types.indexOf(t.label) >= 0).map(t => '- ' + t.label + '：' + t.def).join('\n') + '\n\n' +
    '【關係類型】（relation.type 用 ID）\n' + defs.rels.map(r => '- ' + r.id + '（' + r.label + '）：' + r.def).join('\n') + '\n\n' +
    '【節點清單】（ID｜名稱（同義說法）｜類型｜所屬分類）\n' + list + '\n\n' +
    '格式：{"items":[{"chunk":1,"main":["id"],"sub":[],"audience":[],"age":"","context":[],"new":[]}]}\n\n' +
    '文件標題：' + title + '\n\n' +
    group.map((c, j) => '【段落' + (j + 1) + '】\n' + String(c.text).slice(0, 1500)).join('\n\n');
}

/** chunks：[{id, text}]；回傳 {links:[[段落ID, 節點ID, 標籤]], meta:{段落ID:{audience,age,context}}, cands:[...]} */
function kgMapChunks_(title, chunks) {
  const defs = kgDefs_();
  const nodes = kgNodes_(); const valid = {}; nodes.forEach(r => valid[String(r['節點ID'])] = true);
  const list = kgNodeList_(); const links = [], cands = [], meta = {};
  for (let i = 0; i < chunks.length; i += 12) {
    const group = chunks.slice(i, i + 12);
    try {
      const r = genJson_(cfg_('model_triage'), kgMapPrompt_(defs, list, title, group), { temperature: 0, noThink: true });
      (r.items || []).forEach(it => {
        const c = group[Number(it.chunk) - 1]; if (!c) return;
        const main = Array.from(new Set(it.main || it.nodes || [])).filter(id => valid[id]).slice(0, 3);
        const sub = Array.from(new Set(it.sub || [])).filter(id => valid[id] && main.indexOf(id) < 0).slice(0, 3);
        main.forEach(id => links.push([c.id, id, KG_TAG_MAIN]));
        sub.forEach(id => links.push([c.id, id, KG_TAG_SUB]));
        meta[c.id] = {
          audience: [].concat(it.audience || []).filter(a => KG_AUDIENCES.indexOf(a) >= 0).join('、'),
          age: String(it.age || '').trim().slice(0, 30),
          context: [].concat(it.context || []).map(x => String(x).trim()).filter(Boolean).slice(0, 4).join('、'),
        };
        (it.new || []).forEach(n => { if (n && n.name) cands.push(Object.assign({ chunkId: c.id }, n)); });
      });
    } catch (e) { console.warn('節點標註失敗', e); }
  }
  return { links: links, cands: cands, meta: meta };
}

/** 寫入標註與候選；候選名稱和既有節點（含同義說法）相同就直接掛上，和待審候選相同就累加次數 */
function kgSaveMapping_(docId, res) {
  const now = new Date(); const defs = kgDefs_(); const relIds = kgRelIds_(defs); const types = kgNewTypes_(defs);
  const nodes = kgNodes_(); const byName = {};
  nodes.forEach(r => { byName[String(r['名稱']).trim()] = String(r['節點ID']); String(r['同義說法'] || '').split(/[、,，]/).map(x => x.trim()).filter(Boolean).forEach(x => byName[x] = String(r['節點ID'])); });
  const valid = {}; nodes.forEach(r => valid[String(r['節點ID'])] = true);
  const links = res.links.slice();
  const cs = sh_('kgCands'); const head = kgHead_('kgCands'); const C = h => head.indexOf(h) + 1;
  const pending = rows_('kgCands').filter(r => r['狀態'] === '待審核');
  let added = 0, merged = 0;
  res.cands.forEach(n => {
    const name = String(n.name).trim().slice(0, 40);
    if (byName[name]) { links.push([n.chunkId, byName[name], KG_TAG_MAIN]); return; }
    const p = pending.find(r => String(r['名稱']).trim() === name);
    if (p) {
      const ids = String(p['段落ID'] || '').split(';').filter(Boolean); if (ids.indexOf(n.chunkId) < 0) ids.push(n.chunkId);
      p['段落ID'] = ids.join(';'); p['次數'] = Number(p['次數'] || 1) + 1;
      cs.getRange(p._row, C('段落ID')).setValue(p['段落ID']); cs.getRange(p._row, C('次數')).setValue(p['次數']); merged++; return;
    }
    const rel = n.relation && relIds.indexOf(n.relation.type) >= 0 && n.relation.type !== 'hasPart' && valid[n.relation.target] ? JSON.stringify({ type: n.relation.type, target: n.relation.target }) : '';
    const row = ['C' + Utilities.getUuid().slice(0, 8), name, String(n.definition || '').slice(0, 200), valid[n.parent] ? n.parent : '', types.indexOf(n.type) >= 0 ? n.type : '概念',
      rel, docId, n.chunkId, 1, String(n.reason || '').slice(0, 200), '待審核', '', '', ''];
    append_('kgCands', [row]);
    pending.push({ _row: cs.getLastRow(), '名稱': name, '段落ID': n.chunkId, '次數': 1, '狀態': '待審核' });
    added++;
  });
  // 同一段同一節點只留一筆；同時被標主要與次要時以主要為準
  const best = {};
  links.forEach(l => { const k = l[0] + '|' + l[1]; if (!best[k] || l[2] === KG_TAG_MAIN) best[k] = l; });
  const lh = kgHead_('kgLinks');
  const rows = Object.keys(best).map(k => { const l = best[k]; const o = { '段落ID': l[0], '節點ID': l[1], '方式': 'AI', '時間': now, '標籤': l[2] }; return lh.map(h => o[h] == null ? '' : o[h]); });
  if (rows.length) append_('kgLinks', rows);
  // 讀者對象、適用年齡、情境寫回段落
  const meta = res.meta || {};
  if (Object.keys(meta).length) {
    const s = sh_('chunks'); const ch = kgHead_('chunks');
    const ai = ch.indexOf('讀者對象'), gi = ch.indexOf('適用年齡'), xi = ch.indexOf('情境');
    if (ai >= 0 && gi >= 0 && xi >= 0) rows_('chunks').forEach(r => {
      const m = meta[String(r['段落ID'])]; if (!m) return;
      s.getRange(r._row, ai + 1).setValue(safeCell_(m.audience)); s.getRange(r._row, gi + 1).setValue(safeCell_(m.age)); s.getRange(r._row, xi + 1).setValue(safeCell_(m.context));
    });
  }
  return { links: rows.length, main: rows.filter(r => r[lh.indexOf('標籤')] === KG_TAG_MAIN).length, candidates: added, candidateHits: merged };
}

/** 標註一份文件中還沒有標過的段落 */
function kgMapDocument_(docId) {
  if (!kgHasGraph_()) return { skipped: '尚未匯入知識圖譜' };
  const done = kgDoneChunks_();
  const doc = rows_('docs').find(r => String(r['文件ID']) === String(docId)) || {};
  const chunks = rows_('chunks').filter(r => String(r['文件ID']) === String(docId) && !done[String(r['段落ID'])]).map(r => ({ id: String(r['段落ID']), text: String(r['內容']) }));
  if (!chunks.length) return { links: 0, main: 0, candidates: 0, candidateHits: 0 };
  return kgSaveMapping_(docId, kgMapChunks_(String(doc['標題'] || ''), chunks));
}
/** 已標註的段落：至少掛到一個「目前存在」的節點才算（換過圖譜後，掛在舊節點上的段落會被視為未標註，可以重新標） */
function kgDoneChunks_(links) {
  const ids = {}; kgNodes_(true).forEach(n => ids[String(n['節點ID'])] = true);
  const done = {}; (links || rows_('kgLinks')).forEach(r => { if (ids[String(r['節點ID'])]) done[String(r['段落ID'])] = true; });
  return done;
}
/** 還有未標註段落的文件：[{id, title, status, todo}] */
function kgTodoDocs_(links) {
  const done = kgDoneChunks_(links);
  const n = {}; rows_('chunks').forEach(r => { if (!done[String(r['段落ID'])] && String(r['狀態']) !== '停用') n[String(r['文件ID'])] = (n[String(r['文件ID'])] || 0) + 1; });
  return rows_('docs').filter(d => n[String(d['文件ID'])] && String(d['狀態']) !== '停用').map(d => ({ id: String(d['文件ID']), title: String(d['標題']), status: String(d['狀態']), todo: n[String(d['文件ID'])] }));
}
function adminMapDoc(docId) { requireAdmin_(); return kgMapDocument_(docId); }

// ---------- 後台：總覽、覆蓋度、候選 ----------
function adminGraphLoad() {
  requireAdmin_();
  if (!kgHasGraph_()) return { empty: true };
  const defs = kgDefs_();
  const nodes = kgNodes_(true), edges = rows_('kgEdges'), links = rows_('kgLinks');
  const chunk = {}; rows_('chunks').forEach(r => chunk[String(r['段落ID'])] = { status: String(r['狀態']), type: String(r['類型'] || ''), docId: String(r['文件ID']), text: String(r['內容']) });
  const docs = {}; rows_('docs').forEach(r => docs[String(r['文件ID'])] = String(r['標題']));
  const cov = {};
  links.forEach(l => {
    const c = chunk[String(l['段落ID'])]; if (!c) return;
    const k = String(l['節點ID']); const o = cov[k] || (cov[k] = { all: 0, sub: 0, ok: 0, act: 0 });
    // 舊資料沒有「標籤」欄，視為主要
    if (String(l['標籤'] || KG_TAG_MAIN) !== KG_TAG_MAIN) { o.sub++; return; }
    o.all++; if (c.status === '已核可') { o.ok++; if (ACTIONABLE.indexOf(c.type) >= 0) o.act++; }
  });
  const byId = {}; nodes.forEach(r => byId[String(r['節點ID'])] = r);
  const label = id => byId[id] ? String(byId[id]['名稱']) : '';
  const list = nodes.map(r => { const k = String(r['節點ID']); const o = cov[k] || { all: 0, sub: 0, ok: 0, act: 0 };
    return { id: k, label: String(r['名稱']), type: String(r['類型']), path: String(r['路徑'] || ''), status: String(r['狀態'] || '啟用'), from: String(r['來源'] || ''), syn: String(r['同義說法'] || ''), all: o.all, sub: o.sub, ok: o.ok, act: o.act }; });
  const pairs = kgPairs_(nodes, edges);
  const cands = rows_('kgCands').filter(r => r['狀態'] === '待審核').map(r => {
    const ids = String(r['段落ID'] || '').split(';').filter(Boolean); const c = chunk[ids[0]] || {};
    let rel = null; try { rel = r['建議關係'] ? JSON.parse(r['建議關係']) : null; } catch (e) {}
    const type = String(r['類型'] || '概念'); const parent = String(r['建議上層'] || '');
    const warns = [];
    if (parent && byId[parent]) { const w = kgRelWarn_(pairs, defs, 'hasPart', String(byId[parent]['類型']), type); if (w) warns.push('上層：' + w); }
    if (rel && byId[rel.target]) { const w = kgRelWarn_(pairs, defs, rel.type, type, String(byId[rel.target]['類型'])); if (w) warns.push('建議關係：' + w); }
    return { id: String(r['候選ID']), name: String(r['名稱']), def: String(r['定義']), parent: parent, parentLabel: label(parent), type: type,
      rel: rel ? { type: rel.type, label: kgRelLabel_(defs, rel.type), target: rel.target, targetLabel: label(rel.target) || rel.target } : null,
      warns: warns, doc: docs[String(r['文件ID'])] || String(r['文件ID']), count: Number(r['次數'] || 1), reason: String(r['理由'] || ''), snippet: String(c.text || '').slice(0, 160) };
  }).sort((a, b) => b.count - a.count);
  const nodeTypes = {}; nodes.forEach(r => nodeTypes[String(r['節點ID'])] = String(r['類型']));
  return {
    stats: { nodes: nodes.filter(r => String(r['狀態'] || '啟用') !== '停用').length, edges: edges.length, sources: Math.max(0, sh_('kgSources').getLastRow() - 1),
      links: links.length, covered: list.filter(n => n.ok > 0).length, pending: cands.length },
    nodes: list, cands: cands, types: kgNewTypes_(defs), pairs: pairs, nodeTypes: nodeTypes, todo: kgTodoDocs_(links),
  };
}

/** action：approve（opts 可改 name/def/parent/type）、merge（opts.target）、skip */
function adminCandAction(candId, action, opts) {
  const me = requireAdmin_(); opts = opts || {};
  const defs = kgDefs_();
  const r = rows_('kgCands').find(x => String(x['候選ID']) === String(candId));
  if (!r) throw new Error('找不到候選'); if (r['狀態'] !== '待審核') throw new Error('這個候選已處理過');
  const cs = sh_('kgCands'); const head = kgHead_('kgCands'); const C = h => head.indexOf(h) + 1;
  const now = new Date(); const chunkIds = String(r['段落ID'] || '').split(';').filter(Boolean);
  const nodes = kgNodes_(true); const byId = {}; nodes.forEach(n => byId[String(n['節點ID'])] = n);
  const lh = kgHead_('kgLinks'); const linkRow = (c, id, how) => { const o = { '段落ID': c, '節點ID': id, '方式': how, '時間': now, '標籤': KG_TAG_MAIN }; return lh.map(h => o[h] == null ? '' : o[h]); };
  let result = '';
  if (action === 'approve') {
    const name = String(opts.name || r['名稱']).trim(); if (!name) throw new Error('請填名稱');
    const type = String(opts.type || r['類型'] || '概念');
    if (kgNewTypes_(defs).indexOf(type) < 0) throw new Error('節點類型不正確（主軸不接受新增）');
    const parent = byId[opts.parent || r['建議上層']] ? String(opts.parent || r['建議上層']) : '';
    const id = 'n_' + Utilities.getUuid().slice(0, 8);
    const pathTxt = parent ? [String(byId[parent]['路徑'] || ''), String(byId[parent]['名稱'])].filter(Boolean).join(' > ') : '';
    append_('kgNodes', [[id, name, type, parent, pathTxt, String(opts.def != null ? opts.def : r['定義']), '', '', '', '啟用', KG_FROM_PROPOSAL, now]]);
    const edges = [];
    if (parent) edges.push([parent, 'hasPart', id, kgRelLabel_(defs, 'hasPart'), KG_FROM_PROPOSAL]);
    try { const rel = r['建議關係'] ? JSON.parse(r['建議關係']) : null; if (rel && byId[rel.target]) edges.push([id, rel.type, rel.target, kgRelLabel_(defs, rel.type), KG_FROM_PROPOSAL]); } catch (e) {}
    if (edges.length) append_('kgEdges', edges);
    if (chunkIds.length) append_('kgLinks', chunkIds.map(c => linkRow(c, id, '候選核可')));
    result = id;
  } else if (action === 'merge') {
    const t = byId[opts.target]; if (!t) throw new Error('請選擇要合併到的節點');
    const syn = String(t['同義說法'] || '').split('、').filter(Boolean); if (syn.indexOf(r['名稱']) < 0) syn.push(String(r['名稱']));
    sh_('kgNodes').getRange(t._row, kgHead_('kgNodes').indexOf('同義說法') + 1).setValue(safeCell_(syn.join('、')));
    if (chunkIds.length) append_('kgLinks', chunkIds.map(c => linkRow(c, String(opts.target), '候選合併')));
    result = String(opts.target);
  } else if (action !== 'skip') throw new Error('未知的操作');
  cs.getRange(r._row, C('狀態')).setValue(action === 'approve' ? '已核可' : action === 'merge' ? '已合併' : '略過');
  cs.getRange(r._row, C('處理結果')).setValue(result);
  cs.getRange(r._row, C('審核者')).setValue(me);
  cs.getRange(r._row, C('審核時間')).setValue(now);
  return { ok: true, result: result };
}

// ---------- 依現有文件產生知識圖譜與 Ontology（只產生檔案供下載，不會取代系統裡的圖譜） ----------
// 流程分三段由後台網頁逐批呼叫，避免單次超過 Apps Script 6 分鐘上限：
//  1. adminKgGenStart：統計已核可段落，決定批次
//  2. adminKgGenBatch：每批約 15 段，AI 抽出概念、類型、定義、別名、與其他概念的關係
//  3. adminKgGenFinish：合併同義概念、AI 整理成主軸／子分類階層，輸出 knowledge-graph.json 與 ontology.ttl
const KG_GEN_BATCH = 15;
function kgGenChunks_() {
  const docs = {}; rows_('docs').forEach(r => { if (String(r['狀態']) === '已核可') docs[String(r['文件ID'])] = String(r['標題']); });
  return rows_('chunks').filter(r => docs[String(r['文件ID'])] && String(r['狀態']) === '已核可')
    .map(r => ({ id: String(r['段落ID']), docId: String(r['文件ID']), title: docs[String(r['文件ID'])], text: String(r['內容']) }));
}
function adminKgGenStart() {
  requireAdmin_();
  const ch = kgGenChunks_();
  const docN = new Set(ch.map(c => c.docId)).size;
  return { chunks: ch.length, docs: docN, batches: Math.ceil(ch.length / KG_GEN_BATCH), few: docN < 10 || ch.length < 80 };
}
function adminKgGenBatch(i) {
  requireAdmin_();
  const defs = kgDefs_(); const types = kgNewTypes_(defs).filter(t => t !== '子分類');
  const group = kgGenChunks_().slice(i * KG_GEN_BATCH, (i + 1) * KG_GEN_BATCH);
  if (!group.length) return { items: [] };
  const r = genJson_(cfg_('model_answer'), '你在從幼教專業文件建立知識圖譜，只輸出 JSON。\n' +
    '從下面段落抽出重要的「概念」：專業概念、實務策略、理論、需求類別、法規課綱、課程取向等。只抽段落真正在談、對老師有用的，不要抽一般詞彙。\n' +
    '每個概念：name（簡短標準名稱）、type（從下列類型選）、definition（一句話，依段落內容）、aliases（段落裡出現的其他說法，可空）、' +
    'tips（段落中可直接照做的實務要點，0–2 條，可空）、related（與同批其他概念的關係：{"rel":關係ID,"target":概念 name}，方向為「本概念 → target」，可空）、chunks（出現在哪幾段，填段落編號）。\n\n' +
    '【類型】\n' + defs.types.filter(t => types.indexOf(t.label) >= 0).map(t => '- ' + t.label + '：' + t.def).join('\n') + '\n\n' +
    '【關係】\n' + defs.rels.filter(x => x.id !== 'hasPart').map(x => '- ' + x.id + '（' + x.label + '）：' + x.def).join('\n') + '\n\n' +
    '格式：{"concepts":[{"name":"","type":"","definition":"","aliases":[],"tips":[],"related":[],"chunks":[1]}]}\n\n' +
    group.map((c, j) => '【段落' + (j + 1) + '｜' + c.title + '】\n' + c.text.slice(0, 1200)).join('\n\n'), { temperature: 0 });
  const relIds = kgRelIds_(defs);
  const items = (r.concepts || []).filter(c => c && c.name).map(c => ({
    name: String(c.name).trim().slice(0, 40), type: types.indexOf(c.type) >= 0 ? c.type : '概念', def: String(c.definition || '').slice(0, 200),
    aliases: [].concat(c.aliases || []).map(String).map(s => s.trim()).filter(Boolean).slice(0, 6),
    tips: [].concat(c.tips || []).map(String).filter(Boolean).slice(0, 2),
    related: [].concat(c.related || []).filter(x => x && relIds.indexOf(x.rel) >= 0 && x.rel !== 'hasPart' && x.target).map(x => ({ rel: x.rel, target: String(x.target).trim() })),
    docs: Array.from(new Set([].concat(c.chunks || []).map(n => (group[Number(n) - 1] || {}).docId).filter(Boolean))),
  }));
  return { items: items };
}
function adminKgGenFinish(items) {
  const me = requireAdmin_();
  const defs = kgDefs_();
  // 1. 依名稱與別名合併
  const norm = s => String(s || '').replace(/[\s「」『』（）()]/g, '').toLowerCase();
  const byKey = {}, list = [];
  items.forEach(it => {
    const keys = [it.name].concat(it.aliases).map(norm).filter(Boolean);
    let c = keys.map(k => byKey[k]).find(Boolean);
    if (!c) { c = { name: it.name, types: {}, defs: [], aliases: {}, tips: [], related: [], docs: {}, n: 0 }; list.push(c); }
    c.n++; c.types[it.type] = (c.types[it.type] || 0) + 1;
    if (it.def) c.defs.push(it.def);
    it.aliases.concat([it.name]).forEach(a => { if (a !== c.name) c.aliases[a] = 1; });
    it.tips.forEach(t => { if (c.tips.indexOf(t) < 0) c.tips.push(t); });
    c.related = c.related.concat(it.related);
    it.docs.forEach(d => c.docs[d] = 1);
    keys.forEach(k => byKey[k] = c);
  });
  // 只保留出現較多的概念，避免太多雜訊（最多 300 個）
  list.sort((a, b) => b.n - a.n || Object.keys(b.docs).length - Object.keys(a.docs).length);
  const keep = list.slice(0, 300);
  // ID 加上產生時間，避免和之前匯入的圖譜或上一次產生的結果撞號
  const pre = 'g' + Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyMMddHHmm') + '_';
  keep.forEach((c, i) => { c.id = 'c' + (i + 1); c.type = Object.keys(c.types).sort((a, b) => c.types[b] - c.types[a])[0]; });
  // 2. AI 整理階層：主軸（4–6 個）→ 子分類 → 概念；並合併仍重複的概念
  const h = genJson_(cfg_('model_answer'), '你在整理幼教知識圖譜的階層，只輸出 JSON。\n' +
    '下面是從文件抽出的概念（ID｜名稱｜類型｜出現次數）。請：\n' +
    '1. 定出 4–6 個「主軸」（最上層大類），每個主軸下 2–8 個「子分類」。\n' +
    '2. 把每個概念放進最適合的子分類（parent 填子分類 ID）；若某概念其實是另一個概念的下位項，parent 可填該概念 ID。\n' +
    '3. 若兩個概念其實是同一件事，在 merge 列出 {"from":被合併ID,"into":保留ID}。\n' +
    '格式：{"hubs":[{"id":"h1","label":""}],"subs":[{"id":"s1","label":"","hub":"h1","definition":""}],"place":[{"id":"c1","parent":"s1"}],"merge":[{"from":"c9","into":"c2"}]}\n\n' +
    keep.map(c => c.id + '｜' + c.name + '｜' + c.type + '｜' + c.n).join('\n'), { temperature: 0 });
  const byId = {}; keep.forEach(c => byId[c.id] = c);
  const mergeTo = {}; (h.merge || []).forEach(m => { if (byId[m.from] && byId[m.into] && m.from !== m.into) mergeTo[m.from] = m.into; });
  const root = id => { let x = id, n = 0; while (mergeTo[x] && n++ < 10) x = mergeTo[x]; return x; };
  Object.keys(mergeTo).forEach(f => { const t = byId[root(f)], s = byId[f]; t.aliases[s.name] = 1; Object.keys(s.aliases).forEach(a => t.aliases[a] = 1);
    s.tips.forEach(x => { if (t.tips.indexOf(x) < 0) t.tips.push(x); }); t.related = t.related.concat(s.related); Object.keys(s.docs).forEach(d => t.docs[d] = 1); t.n += s.n; });
  const nodesC = keep.filter(c => !mergeTo[c.id]);
  const hubs = (h.hubs || []).filter(x => x && x.id && x.label).slice(0, 6);
  const hubIds = {}; hubs.forEach(x => hubIds[x.id] = x);
  const subs = (h.subs || []).filter(x => x && x.id && x.label && hubIds[x.hub]);
  const subIds = {}; subs.forEach(x => subIds[x.id] = x);
  const place = {}; (h.place || []).forEach(p => { if (p && p.id && byId[p.id] && !mergeTo[p.id] && !(p.id in place)) place[p.id] = p.parent; });
  // 3. 組 JSON（與匯入格式相同）
  const typeId = {}; defs.types.forEach(t => typeId[t.label] = t.id);
  const relLabel = id => kgRelLabel_(defs, id);
  const docs = {}; rows_('docs').forEach(r => docs[String(r['文件ID'])] = r);
  const nodes = [], edges = [];
  hubs.forEach(x => nodes.push({ id: x.id, label: x.label, group: x.id, groupLabel: x.label, type: typeId['主軸'] || 'hub', typeLabel: '主軸', parent: '', path: [], definition: '', practiceTips: [], sources: [] }));
  subs.forEach(x => { nodes.push({ id: x.id, label: x.label, group: x.hub, groupLabel: hubIds[x.hub].label, type: typeId['子分類'] || 'sub', typeLabel: '子分類', parent: x.hub, path: [hubIds[x.hub].label], definition: String(x.definition || ''), practiceTips: [], sources: [] });
    edges.push({ source: x.hub, target: x.id, label: relLabel('hasPart'), relation: 'hasPart' }); });
  const valid = {}; nodesC.forEach(c => valid[c.id] = c);
  const parent0 = c => { const p = place[c.id]; if (p && valid[root(p)] && root(p) !== c.id) return root(p); return subIds[p] ? p : ''; };
  // 概念互為上下層（形成循環）時，拿掉造成循環的那一層
  const parentOf = c => { const p = parent0(c); let x = p, n = 0; while (x && valid[x] && n++ < 20) { if (x === c.id) return ''; x = parent0(valid[x]); } return p; };
  const pathOf = (id, seen) => {
    if (hubIds[id]) return [hubIds[id].label];
    if (subIds[id]) return [hubIds[subIds[id].hub].label, subIds[id].label];
    const c = valid[id]; if (!c || (seen = seen || {})[id]) return []; seen[id] = 1;
    const p = parentOf(c); return p ? pathOf(p, seen).concat([c.name]) : [c.name];
  };
  nodesC.forEach(c => {
    const p = parentOf(c); const path = p ? pathOf(p) : [];
    const sub = subIds[p] || null; const hub = sub ? hubIds[sub.hub] : null;
    nodes.push({ id: c.id, label: c.name, group: hub ? hub.id : '', groupLabel: hub ? hub.label : '', type: typeId[c.type] || 'concept', typeLabel: c.type,
      parent: p, path: path, definition: c.defs.sort((a, b) => b.length - a.length)[0] || '', practiceTips: c.tips.slice(0, 4), sources: Object.keys(c.docs),
      aliases: Object.keys(c.aliases).slice(0, 8), mentions: c.n });
    if (p) edges.push({ source: p, target: c.id, label: relLabel('hasPart'), relation: 'hasPart' });
  });
  const nameTo = {}; nodesC.forEach(c => { nameTo[norm(c.name)] = c.id; Object.keys(c.aliases).forEach(a => { if (!nameTo[norm(a)]) nameTo[norm(a)] = c.id; }); });
  const seenE = {};
  nodesC.forEach(c => c.related.forEach(r => {
    const t = nameTo[norm(r.target)]; if (!t || t === c.id) return;
    const k = c.id + '|' + r.rel + '|' + t; if (seenE[k]) return; seenE[k] = 1;
    edges.push({ source: c.id, target: t, label: relLabel(r.rel), relation: r.rel });
  }));
  nodes.forEach(n => { n.id = pre + n.id; if (n.parent) n.parent = pre + n.parent; if (n.group) n.group = pre + n.group; });
  edges.forEach(e => { e.source = pre + e.source; e.target = pre + e.target; });
  const usedDocs = {}; nodesC.forEach(c => Object.keys(c.docs).forEach(d => usedDocs[d] = 1));
  const sources = Object.keys(usedDocs).map(d => ({ id: d, title: String((docs[d] || {})['標題'] || d), publisher: String((docs[d] || {})['出處'] || ''), url: '', scope: String((docs[d] || {})['證據等級'] || '') }));
  const now = new Date();
  const json = {
    meta: { title: '知識圖譜（依現有文件自動產生，待專家審核）', version: Utilities.formatDate(now, 'Asia/Taipei', 'yyyy.MM.dd-HHmm'), date: Utilities.formatDate(now, 'Asia/Taipei', 'yyyy-MM-dd'),
      generatedBy: me, note: '由 AI 從已核可文件抽取，節點與關係需專家逐一確認後再匯入。', counts: { nodes: nodes.length, edges: edges.length, sources: sources.length }, extracted: items.length, merged: list.length },
    groups: hubs.map(x => ({ id: pre + x.id, label: x.label })),
    nodeTypes: defs.types.map(t => ({ id: t.id, label: t.label, definition: t.def })),
    relations: defs.rels.map(r => ({ id: r.id, label: r.label, definition: r.def })),
    sources: sources, nodes: nodes, edges: edges,
  };
  return { json: JSON.stringify(json, null, 2), ttl: kgOntologyTtl_(defs, json.meta), stats: json.meta.counts, extracted: items.length, merged: list.length, kept: nodesC.length };
}

/** Ontology（架構）：節點類型為類別、關係為物件屬性，加上文章標註屬性；與匯入的 ontology.ttl 同結構 */
function kgOntologyTtl_(defs, meta) {
  const q = s => '"' + String(s || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, ' ') + '"@zh-Hant';
  const cls = id => 'kg:' + String(id).replace(/(^|[_-])(\w)/g, (m, a, b) => b.toUpperCase()).replace(/[^A-Za-z0-9]/g, '');
  const L = [];
  L.push('@prefix kg: <https://example.org/ece-kg/> .', '@prefix owl: <http://www.w3.org/2002/07/owl#> .', '@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .',
    '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .', '@prefix skos: <http://www.w3.org/2004/02/skos/core#> .', '@prefix dct: <http://purl.org/dc/terms/> .',
    '@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .', '',
    '<https://example.org/ece-kg/ontology> a owl:Ontology ;', '  dct:title ' + q('知識圖譜 Ontology（依現有文件產生，待專家審核）') + ' ;', '  owl:versionInfo "' + meta.version + '" .', '',
    '#################### 類別 ####################',
    'kg:KnowledgeNode a owl:Class ; rdfs:label ' + q('知識節點') + ' .');
  defs.types.forEach(t => L.push(cls(t.id) + ' a owl:Class ; rdfs:subClassOf kg:KnowledgeNode ;', '  rdfs:label ' + q(t.label) + ' ;', '  skos:definition ' + q(t.def) + ' .'));
  L.push('kg:Source a owl:Class ; rdfs:label ' + q('資料來源') + ' .', 'kg:Article a owl:Class ; rdfs:label ' + q('文章') + ' .',
    'kg:Audience a owl:Class ; rdfs:label ' + q('讀者對象') + ' .');
  KG_AUDIENCES.forEach((a, i) => L.push('kg:audience' + (i + 1) + ' a kg:Audience ; rdfs:label ' + q(a) + ' .'));
  L.push('', '#################### 關係 ####################',
    'kg:semanticRelation a owl:ObjectProperty ; rdfs:subPropertyOf skos:related ; rdfs:label ' + q('語意關聯') + ' ;', '  rdfs:domain kg:KnowledgeNode ; rdfs:range kg:KnowledgeNode .');
  defs.rels.forEach(r => {
    if (r.id === 'hasPart') L.push('kg:hasPart a owl:ObjectProperty , owl:TransitiveProperty ; rdfs:subPropertyOf skos:narrowerTransitive ;', '  rdfs:label ' + q(r.label) + ' ; owl:inverseOf kg:partOf ;', '  rdfs:domain kg:KnowledgeNode ; rdfs:range kg:KnowledgeNode ;', '  skos:definition ' + q(r.def) + ' .', 'kg:partOf a owl:ObjectProperty , owl:TransitiveProperty ; rdfs:label ' + q('屬於') + ' .');
    else L.push('kg:' + r.id + ' a owl:ObjectProperty' + (r.id === 'relatedTo' ? ' , owl:SymmetricProperty' : '') + ' ; rdfs:subPropertyOf kg:semanticRelation ;', '  rdfs:label ' + q(r.label) + ' ;', '  skos:definition ' + q(r.def) + ' .');
  });
  L.push('', '#################### 資料屬性 ####################',
    'kg:practiceTip a owl:DatatypeProperty ; rdfs:label ' + q('實務要點') + ' ; rdfs:domain kg:KnowledgeNode ; rdfs:range rdf:langString .',
    'kg:hasSource a owl:ObjectProperty ; rdfs:subPropertyOf dct:source ; rdfs:label ' + q('資料來源') + ' ; rdfs:domain kg:KnowledgeNode ; rdfs:range kg:Source .',
    '', '#################### 文章標註 ####################',
    'kg:primaryTag a owl:ObjectProperty ; rdfs:label ' + q('主要標籤') + ' ; rdfs:domain kg:Article ; rdfs:range kg:KnowledgeNode ; skos:definition ' + q('文章直接討論的主題。') + ' .',
    'kg:secondaryTag a owl:ObjectProperty ; rdfs:label ' + q('次要標籤') + ' ; rdfs:domain kg:Article ; rdfs:range kg:KnowledgeNode ; skos:definition ' + q('文章隱含的解釋、理論或策略。') + ' .',
    'kg:audience a owl:ObjectProperty ; rdfs:label ' + q('讀者對象') + ' ; rdfs:domain kg:Article ; rdfs:range kg:Audience .',
    'kg:targetAge a owl:DatatypeProperty ; rdfs:label ' + q('適用年齡') + ' ; rdfs:domain kg:Article ; rdfs:range xsd:string .',
    'kg:context a owl:DatatypeProperty ; rdfs:label ' + q('情境') + ' ; rdfs:domain kg:Article ; rdfs:range rdf:langString .',
    'kg:proposedNode a owl:DatatypeProperty ; rdfs:label ' + q('建議新增節點') + ' ; rdfs:domain kg:Article ; rdfs:range xsd:string .', '');
  return L.join('\n');
}
