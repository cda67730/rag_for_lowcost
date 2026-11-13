/**
 * 模型呼叫工具：產生、JSON、向量；fetchAll 用來並行執行
 * 支援 Gemini 與 OpenAI：依模型 ID 自動判斷（gpt-、o1/o3…、text-embedding- 開頭走 OpenAI），其他程式不用改
 */

const GEMINI_BASE = 'https://generativelanguage.googleapis.com/v1beta/models/';
const OPENAI_BASE = 'https://api.openai.com/v1/';

function apiKey_() {
  const k = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');
  if (!k) throw new Error('GEMINI_API_KEY 未設定');
  return k;
}
function openaiKey_() {
  const k = PropertiesService.getScriptProperties().getProperty('OPENAI_API_KEY');
  if (!k) throw new Error('OPENAI_API_KEY 未設定（專案設定 → 指令碼屬性）');
  return k;
}
function isOpenAI_(model) { return /^(gpt-|o\d|chatgpt-|text-embedding-)/i.test(String(model || '')); }

/** OpenAI Chat Completions 請求。推理模型不接受溫度，所以一律不送溫度；不需要思考的任務把推理強度降到最低 */
function openaiReq_(model, parts, opts) {
  const content = parts.map(p => p.inline_data
    ? { type: 'file', file: { filename: 'document.pdf', file_data: 'data:' + p.inline_data.mime_type + ';base64,' + p.inline_data.data } }
    : { type: 'text', text: p.text });
  const body = { model: model, messages: [{ role: 'user', content: content }] };
  if (opts.json) body.response_format = { type: 'json_object' };
  if (opts.maxTokens) body.max_completion_tokens = opts.maxTokens;
  // none：GPT-6 Luna、GPT-6 Sol、GPT-6.1 Terra 支援；其他用 low。不接受時 genFix_ 會拿掉重送
  if (opts.noThink) body.reasoning_effort = /luna|terra|^gpt-6-sol$/.test(model) ? 'none' : 'low';
  return {
    url: OPENAI_BASE + 'chat/completions', method: 'post', contentType: 'application/json',
    payload: JSON.stringify(body), headers: { Authorization: 'Bearer ' + openaiKey_() }, muteHttpExceptions: true,
  };
}

/** 建立一個產生請求（可放進 fetchAll） */
/** 模型主版本：gemini-2.5-flash → 2.5；gemini-3.1-pro-preview → 3.1；看不出來回傳 0 */
function modelVer_(model) { const m = String(model || '').match(/gemini-(\d+(?:\.\d+)?)/); return m ? Number(m[1]) : 0; }

function genReq_(model, parts, opts) {
  opts = opts || {};
  if (isOpenAI_(model)) return openaiReq_(model, parts, opts);
  const v3 = modelVer_(model) >= 3;
  const body = { contents: [{ role: 'user', parts: parts }], generationConfig: {} };
  // Gemini 3 以上官方建議溫度維持預設 1.0，調低可能重複或變差，所以不送溫度；2.5 以前照設定
  if (!v3) body.generationConfig.temperature = opts.temperature == null ? 0.3 : opts.temperature;
  if (opts.json) body.generationConfig.responseMimeType = 'application/json';
  if (opts.maxTokens) body.generationConfig.maxOutputTokens = opts.maxTokens;
  // 分流、檢查這類單一任務不需要模型「思考」，關掉可省下數秒
  //  2.5 Flash 系列：thinkingBudget 0；3 以上無法完全關閉：3.1 Flash-Lite 用 minimal，其他用各型號都支援的 low
  //  （3.8 Flash 不支援 minimal；若某個型號不接受，genFix_ 會拿掉思考設定重送）
  if (opts.noThink && v3) body.generationConfig.thinkingConfig = { thinkingLevel: /3\.1-flash-lite/.test(model) ? 'minimal' : 'low' };
  else if (opts.noThink && /2\.5-flash/.test(model)) body.generationConfig.thinkingConfig = { thinkingBudget: 0 };
  return {
    url: GEMINI_BASE + model + ':generateContent', method: 'post', contentType: 'application/json',
    payload: JSON.stringify(body), headers: { 'x-goog-api-key': apiKey_() }, muteHttpExceptions: true,
  };
}

function genText_(res) {
  if (res.getResponseCode() !== 200) throw new Error('模型 API ' + res.getResponseCode() + ': ' + res.getContentText().slice(0, 200));
  const d = JSON.parse(res.getContentText());
  if (d.choices) return String((((d.choices[0] || {}).message) || {}).content || '').trim(); // OpenAI
  const parts = (((d.candidates || [])[0] || {}).content || {}).parts || [];
  return parts.filter(p => !p.thought).map(p => p.text || '').join('').trim(); // 略過思考摘要
}

// ---------- 穩定度紀錄：每回合重設，寫進對話紀錄的「穩定度」欄 ----------
// STAB_.at：目前在哪一步（L4／A1／L5／重排）；STAB_.ev：[{at, kind, note}]
let STAB_ = null;
function stabReset_() { STAB_ = { at: '', ev: [] }; return STAB_; }
function stabAt_(at) { if (STAB_) STAB_.at = at; }
function stabNote_(kind, note) { if (STAB_ && STAB_.ev.length < 30) STAB_.ev.push({ at: STAB_.at, kind: kind, note: String(note || '').slice(0, 120) }); }

function parseJson_(text) {
  try { return JSON.parse(text); } catch (e) {}
  const m = String(text).match(/[\[{][\s\S]*[\]}]/);
  if (m) { try { const v = JSON.parse(m[0]); stabNote_('JSON補救', '回覆夾雜非 JSON 文字，已擷取'); return v; } catch (e) {} }
  stabNote_('JSON失敗', String(text).slice(0, 80));
  throw new Error('模型回覆不是有效的 JSON');
}

function gen_(model, prompt, opts) {
  const req = genReq_(model, [{ text: prompt }], opts); const a = reqArgs_(req);
  return genText_(genFix_(req, UrlFetchApp.fetch(a[0], a[1])));
}

/** 新模型不接受某些參數（思考設定、溫度）而回 400 時，拿掉這些參數重送一次 */
function genFix_(req, res) {
  if (res.getResponseCode() !== 400 || !/thinking|temperature|top_?p|top_?k|reasoning|unsupported/i.test(res.getContentText())) return res;
  const body = JSON.parse(req.payload);
  if (body.generationConfig) ['thinkingConfig', 'temperature', 'topP', 'topK'].forEach(k => delete body.generationConfig[k]);
  ['reasoning_effort', 'temperature', 'top_p'].forEach(k => delete body[k]);
  const a = reqArgs_(Object.assign({}, req, { payload: JSON.stringify(body) }));
  console.warn('模型不接受部分參數，已拿掉後重送：' + req.url);
  stabNote_('參數不相容', '已拿掉思考／溫度設定重送');
  return UrlFetchApp.fetch(a[0], a[1]);
}
function genJson_(model, prompt, opts) {
  return parseJson_(gen_(model, prompt, Object.assign({ json: true }, opts || {})));
}
function reqArgs_(r) { const u = r.url; const o = Object.assign({}, r); delete o.url; return [u, o]; }

/** 向量：每批最多 100 筆 */
function embedReqs_(texts, taskType) {
  const model = cfg_('model_embed');
  const dim = Number(cfg_('embed_dim') || 768);
  const reqs = [];
  if (isOpenAI_(model)) {
    for (let i = 0; i < texts.length; i += 100) reqs.push({
      url: OPENAI_BASE + 'embeddings', method: 'post', contentType: 'application/json', muteHttpExceptions: true,
      payload: JSON.stringify({ model: model, input: texts.slice(i, i + 100).map(t => String(t).slice(0, 8000)), dimensions: dim }),
      headers: { Authorization: 'Bearer ' + openaiKey_() },
    });
    return reqs;
  }
  for (let i = 0; i < texts.length; i += 100) {
    const batch = texts.slice(i, i + 100).map(t => ({
      model: 'models/' + model, content: { parts: [{ text: String(t).slice(0, 8000) }] }, taskType: taskType, outputDimensionality: dim,
    }));
    reqs.push({
      url: GEMINI_BASE + model + ':batchEmbedContents', method: 'post', contentType: 'application/json',
      payload: JSON.stringify({ requests: batch }), headers: { 'x-goog-api-key': apiKey_() }, muteHttpExceptions: true,
    });
  }
  return reqs;
}
function embedParse_(res) {
  if (res.getResponseCode() !== 200) throw new Error('Embedding ' + res.getResponseCode() + ': ' + res.getContentText().slice(0, 200));
  const d = JSON.parse(res.getContentText());
  if (d.data) return d.data.sort((a, b) => a.index - b.index).map(e => e.embedding); // OpenAI
  return d.embeddings.map(e => e.values);
}
function embedTexts_(texts, taskType) {
  if (!texts.length) return [];
  const reqs = embedReqs_(texts, taskType), out = [];
  for (let i = 0; i < reqs.length; i += 3) {           // 一次最多 3 批，避免觸發頻率限制
    const group = reqs.slice(i, i + 3);
    let res = UrlFetchApp.fetchAll(group);
    if (res.some(r => r.getResponseCode() === 429)) { Utilities.sleep(8000); res = UrlFetchApp.fetchAll(group); }
    res.forEach(r => embedParse_(r).forEach(v => out.push(v)));
  }
  return out;
}

/** 向量壓縮：正規化後轉 int8，再轉 base64 存進儲存格 */
function quant_(vec) {
  let n = 0; vec.forEach(x => n += x * x); n = Math.sqrt(n) || 1;
  const bytes = vec.map(x => Math.max(-127, Math.min(127, Math.round((x / n) * 127))));
  return Utilities.base64Encode(bytes);
}
function dequant_(b64) { return Utilities.base64Decode(b64); }
function normVec_(vec) { let n = 0; vec.forEach(x => n += x * x); n = Math.sqrt(n) || 1; return vec.map(x => x / n); }

/** 列出這把 API 金鑰可用的所有模型（快取 6 小時） */
function listModels_(force) {
  const cache = CacheService.getScriptCache();
  if (!force) { const c = cache.get('models'); if (c) return JSON.parse(c); }
  const out = []; let token = '';
  do {
    const res = UrlFetchApp.fetch('https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000' + (token ? '&pageToken=' + encodeURIComponent(token) : ''),
      { headers: { 'x-goog-api-key': apiKey_() }, muteHttpExceptions: true });
    if (res.getResponseCode() !== 200) throw new Error('讀取模型清單失敗 ' + res.getResponseCode() + ': ' + res.getContentText().slice(0, 200));
    const d = JSON.parse(res.getContentText());
    (d.models || []).forEach(m => out.push({ id: String(m.name || '').replace(/^models\//, ''), name: m.displayName || '', desc: String(m.description || '').slice(0, 160),
      methods: m.supportedGenerationMethods || [], input: m.inputTokenLimit || 0, output: m.outputTokenLimit || 0, thinking: !!m.thinking }));
    token = d.nextPageToken || '';
  } while (token);
  try { cache.put('models', JSON.stringify(out), 21600); } catch (e) {}
  return out;
}

/** 列出 OpenAI 金鑰可用的模型（API 只給 ID，沒有能力說明；快取 6 小時） */
function listOpenAIModels_(force) {
  const cache = CacheService.getScriptCache();
  if (!force) { const c = cache.get('models_oa'); if (c) return JSON.parse(c); }
  const res = UrlFetchApp.fetch(OPENAI_BASE + 'models', { headers: { Authorization: 'Bearer ' + openaiKey_() }, muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) throw new Error('讀取 OpenAI 模型清單失敗 ' + res.getResponseCode() + ': ' + res.getContentText().slice(0, 200));
  const out = (JSON.parse(res.getContentText()).data || []).map(m => ({ id: String(m.id), name: '', desc: '', methods: [], input: 0, provider: 'openai' }));
  try { cache.put('models_oa', JSON.stringify(out), 21600); } catch (e) {}
  return out;
}
