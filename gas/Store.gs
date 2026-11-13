/** 試算表存取：設定、Skill、文件、段落、對照表 */

const SHEETS = {
  settings: '設定', skills: 'Skill', skillHist: 'Skill版本', docs: '文件', chunks: '知識段落',
  synonyms: '同義詞', forbidden: '禁止用語', referral: '轉介資源', codes: '邀請碼',
  admins: '管理員', logs: '對話紀錄', feedback: '回饋', evalset: '檢索測試',
  kgNodes: '節點', kgEdges: '關係', kgSources: '圖譜來源', kgLinks: '段落節點', kgCands: '新節點候選', kgDefs: '圖譜定義', evalQs: '評測題庫', evalRuns: '評測結果',
};

function ss_() {
  const id = PropertiesService.getScriptProperties().getProperty('SHEET_ID');
  return id ? SpreadsheetApp.openById(id) : SpreadsheetApp.getActiveSpreadsheet();
}
function sh_(key) {
  const name = SHEETS[key] || key;
  const s = ss_().getSheetByName(name);
  if (!s) throw new Error('找不到工作表「' + name + '」，請先執行 setup()');
  return s;
}
// 舊版標題名稱對照：舊試算表的標題讀進來時自動換成現在程式用的名稱（setup() 也會把舊標題改掉）
const HEADER_ALIAS = { '園所': '單位', '老師訊息（去識別化）': '使用者訊息', '老師用語': '使用者用語', '園所代號': '單位代號' };
/** 讀成物件陣列（第一列為標題） */
function rows_(key) {
  const v = sh_(key).getDataRange().getValues();
  const head = (v.shift() || []).map(h => HEADER_ALIAS[h] || h);
  // 先記下真正的列號再過濾空白列，之後依 _row 更新才不會改錯列
  return v.map((r, i) => { const o = { _row: i + 2 }; head.forEach((h, j) => o[h] = r[j]); o._empty = !r.some(c => c !== ''); return o; }).filter(o => !o._empty);
}
function append_(key, arr) { const s = sh_(key); s.getRange(s.getLastRow() + 1, 1, arr.length, arr[0].length).setValues(safeRows_(arr)); }
// ---------- 寫入安全：避免文字被試算表當成公式（公式注入），也避免 base64 向量以「+」開頭變成錯誤值 ----------
// 開頭是 = + - @（含前置空白、Tab、換行）的文字前面加 '，試算表會當成純文字；讀回來時不會多出 '
function safeCell_(v) { return typeof v === 'string' && /^[\s]*[=+\-@]/.test(v) ? "'" + v : v; }
function safeRows_(arr) { return arr.map(r => r.map(safeCell_)); }

function truthy_(v) { return v === true || String(v).toUpperCase() === 'TRUE' || v === '是'; }

// ---------- 設定（快取 60 秒） ----------
let CFG_ = null;
function cfgAll_() {
  if (CFG_) return CFG_;
  const cache = CacheService.getScriptCache();
  const c = cache.get('cfg');
  if (c) return (CFG_ = JSON.parse(c));
  const o = {};
  rows_('settings').forEach(r => o[r['設定']] = r['值']);
  try { cache.put('cfg', JSON.stringify(o), 60); } catch (e) {}
  return (CFG_ = o);
}
function cfg_(k) { return cfgAll_()[k]; }
function clearCfgCache_() { CFG_ = null; CacheService.getScriptCache().remove('cfg'); CacheService.getScriptCache().remove('index'); }

// ---------- Skill ----------
function skills_() {
  return rows_('skills').filter(r => truthy_(r['啟用'])).map(r => ({
    id: String(r['Skill ID']), name: String(r['名稱']), tags: String(r['對應主題'] || '').split(/[,，、]/).map(s => s.trim()).filter(Boolean),
    always: truthy_(r['一律載入']), content: String(r['內容'] || ''), version: r['版本'],
  }));
}

// ---------- 對照表 ----------
function synonyms_() { return rows_('synonyms').map(r => ({ word: String(r['使用者用語']).trim(), concept: String(r['對應概念']).trim() })).filter(x => x.word); }
function forbidden_() { return rows_('forbidden').map(r => ({ rule: String(r['詞或規則']).trim(), regex: String(r['類型']) === '正規式', note: String(r['說明'] || '') })).filter(x => x.rule); }
function referral_() {
  const o = {};
  rows_('referral').forEach(r => o[String(r['類別'])] = { who: String(r['該找誰'] || ''), contact: String(r['聯絡方式'] || ''), now: String(r['現在要做的事'] || '') });
  return o;
}

// ---------- 邀請碼與頻率 ----------
/** 回傳 { school, trace }；trace 為 true 的邀請碼可以在聊天頁看到流程觀測面板 */
// ---------- 邀請碼錯誤次數限制（Apps Script 拿不到來源 IP，所以用全域計數） ----------
// 10 分鐘內輸錯超過 CODE_FAIL_MAX 次 → 鎖 15 分鐘：期間只接受最近 24 小時內成功用過的邀請碼，新輸入的一律拒絕
const CODE_FAIL_MAX = 20, CODE_FAIL_WINDOW = 600, CODE_LOCK_SECONDS = 900;
function codeLocked_() { return !!CacheService.getScriptCache().get('code_lock'); }
function codeFail_() {
  const c = CacheService.getScriptCache(); const k = 'code_fail_' + Math.floor(Date.now() / 1000 / CODE_FAIL_WINDOW);
  const n = Number(c.get(k) || 0) + 1; c.put(k, String(n), CODE_FAIL_WINDOW);
  if (n >= CODE_FAIL_MAX) c.put('code_lock', '1', CODE_LOCK_SECONDS);
  Utilities.sleep(800);   // 拖慢猜測速度
}
function codeSeen_(code) { return !!CacheService.getScriptCache().get('code_ok_' + String(code).trim().toUpperCase()); }
function codeOk_(code) { try { CacheService.getScriptCache().put('code_ok_' + String(code).trim().toUpperCase(), '1', 86400); } catch (e) {} }
/** 驗證邀請碼並處理錯誤次數；回傳 {info} 或 {error} */
function verifyCode_(code) {
  if (!truthy_(cfg_('enable_invite') == null || cfg_('enable_invite') === '' ? true : cfg_('enable_invite'))) return { info: { school: '公開', trace: false, open: true } };
  if (codeLocked_() && !codeSeen_(code)) return { error: '嘗試次數過多，請 15 分鐘後再試' };
  const info = codeInfo_(code);
  if (!info) { codeFail_(); return { error: '邀請碼不正確或已停用' }; }
  codeOk_(code);
  return { info: info };
}
function codeInfo_(code) {
  if (!code) return null;
  const c = String(code).trim().toUpperCase();
  const hit = rows_('codes').find(r => String(r['邀請碼']).trim().toUpperCase() === c && String(r['啟用']).toUpperCase() !== 'FALSE');
  return hit ? { school: String(hit['單位代號'] || hit['邀請碼']), trace: truthy_(hit['觀測']) } : null;
}
function checkCode_(code) { const i = codeInfo_(code); return i ? i.school : null; }
/** 全機器人每日上限（所有使用者合計） */
function dailyOk_() {
  const cache = CacheService.getScriptCache();
  const key = 'daily_' + Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyyMMdd');
  const n = Number(cache.get(key) || 0) + 1;
  cache.put(key, String(n), 21600);
  return n <= Number(cfg_('daily_limit') || 500);
}
function rateOk_(code) {
  const cache = CacheService.getScriptCache();
  const key = 'rate_' + code + '_' + Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyyMMddHH');
  const n = Number(cache.get(key) || 0) + 1;
  cache.put(key, String(n), 3600);
  return n <= Number(cfg_('hourly_limit') || 300);
}
