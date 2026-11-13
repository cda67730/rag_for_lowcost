# API 合約（兩個後端共用）

聊天頁 `web/chat/` 只認這份合約。Apps Script 版用 `POST /exec`＋`action` 欄位；Python 版用獨立路徑。欄位名稱、型別、語意兩邊必須一致，前端才能不改程式切換後端。

| 動作 | Apps Script | Python（FastAPI） |
| --- | --- | --- |
| 取得介面設定 | `POST /exec {action:"ui"}` | `POST /api/ui` |
| 驗證邀請碼 | `POST /exec {action:"check"}` | `POST /api/check` |
| 問答 | `POST /exec {action:"chat"}` | `POST /api/chat` |
| 回饋 | `POST /exec {action:"feedback"}` | `POST /api/feedback` |
| 文件匯入（後台） | 後台 RPC | `POST /api/ingest` |
| 自動評測（後台） | 後台 RPC | `POST /api/eval/run` |

所有請求與回應皆為 JSON、UTF-8。Apps Script 版因 CORS 限制用 `Content-Type: text/plain`，Python 版接受 `application/json` 與 `text/plain` 兩種。

Python 版每個請求帶 `bot_id`（多機器人；聊天頁從網址 `?bot=<id>` 取得），可再帶 `flow_id` 指定流程，沒帶用該 Bot 的預設流程。Apps Script 版一個部署就是一個機器人，忽略這兩個欄位。階層定義見 `architecture.md`。

---

## 共同欄位

### 回應外層

```json
{ "ok": true, ...payload }
{ "ok": false, "error": "給使用者看的一句話" }
```

錯誤訊息直接顯示給使用者，不要放堆疊或內部細節。

---

## `ui` — 介面設定（不需邀請碼）

請求：`{}`

回應：

```json
{
  "ok": true,
  "invite": true,
  "ui": {
    "botName": "AI 助手",
    "welcome": "歡迎訊息",
    "gate_text": "邀請碼頁說明",
    "input_placeholder": "…", "waiting": "…",
    "referral_title": "…", "referral_note": "…", "referral_heading": "…",
    "ai_tag": "AI 補充", "fb_up": "👍 有幫助", "fb_down": "👎 沒幫助",
    "accent": "#1e7f7d"
  }
}
```

`ui` 裡只放有值的 key；前端缺的用內建預設。`invite=false` 時前端跳過邀請碼頁，直接以空字串呼叫 `check`。

---

## `check` — 驗證邀請碼、取得會話參數

請求：`{ "code": "ABC123" }`

回應：

```json
{ "ok": true, "botName": "…", "welcome": "…", "trace": false, "ui": { …同上… } }
```

- `trace=true`：這組邀請碼開了觀測，之後 `chat` 回應會附 `trace` 陣列，前端顯示「流程」按鈕。
- 邀請碼關閉時任何 `code` 都通過，`trace=false`。
- 錯誤：`邀請碼不正確或已停用`、`嘗試次數過多，請 15 分鐘後再試`。

---

## `chat` — 問答

請求：

```json
{
  "code": "ABC123",
  "session": "前端產生的隨機字串，同一段對話不變",
  "message": "使用者這一則訊息（原文）",
  "history": [ { "role": "teacher" | "bot", "text": "…" } ],
  "nameMap": { "原名": "代稱" },
  "followups": 0,
  "answered": 0,
  "feedback": null
}
```

- `history`：前端保存的對話摘要，最多最近 8 則；`role` 沿用 `teacher`（提問者）與 `bot`。後端不保存會話狀態，所有延續資訊都由前端帶回。
- `nameMap`：去識別化對照表，只存在前端；後端每次回傳更新後的版本。
- `followups`：這個個案已追問過幾次；`answered`：已完整回答幾輪。兩者由前端從上一次回應帶回。

回應（共同欄位）：

```json
{
  "ok": true,
  "type": "answer | continue | ai_fallback | followup | referral | out_of_scope | conservative",
  "nameMap": { "原名": "代稱" },
  "deidText": "去識別化後的訊息",
  "triage": { "intent": "…", "intent_key": "new|more|specific|off", "topics": [], "focus": "…", "in_scope": true, "info_enough": true, "missing": [], "referral": { "category": "…", "level": "無|建議諮詢|需要專人|緊急", "reason": "…" } },
  "intent": "…", "newCase": true, "answered": 1,
  "metrics": { "top": 0.71, "hits": 6, "cited": 3 },
  "quality": { … },
  "schema": [ { "key": "…", "type": "text|list|items", "label": "…", "labelContinue": "…", "modes": "full|continue|both", "required": true, "summary": true, "copy": false } ],
  "stability": { … },
  "ms": 9800,
  "trace": [ { "step": "P03 去識別化", "kind": "text|json|retrieval|prompt|checks", "detail": …, "ms": 120 } ]
}
```

`trace` 只在觀測開啟時附上。`schema` 是這次回答用的欄位定義，前端依它渲染。

各 `type` 的額外欄位：

| type | 額外欄位 |
| --- | --- |
| `answer` / `ai_fallback` / `continue` | `answer`（依 schema 的欄位＋`referral`、`options`、`notice`、`knowledge_gap`）、`sources: [{ref:"K1", title, page, docId}]`、`referral`（物件或 null）、`checks: [{name, pass, note}]`、`rewritten: bool` |
| `followup` | `empathy`、`questions: [最多 2 句]`、`followups`（累計） |
| `referral` | `referral: { category, level, reason, who, contact, now }` |
| `out_of_scope` | `message` |
| `conservative` | `message`、`sources: []`、`checks`（若因檢查未通過） |

`answer` 內 `items` 型別欄位的每個元素：`{ "title": "…", "how": "…", "refs": ["K1"] }`。文字中不出現 refs 編號。

---

## `feedback`

請求：`{ "code", "session", "turn": 3, "kind": "有幫助 | 沒幫助 | 複製：<欄位標題>", "note": "" }`
回應：`{ "ok": true }`

---

## `ingest` — 文件匯入（Python 版後台用；Apps Script 版走後台 RPC）

請求（multipart 或 JSON）：`{ "kb_id", "title", "source": "upload|paste", "content_type", "data": "<base64 或純文字>", "approve": false }`（文件屬於知識庫，不屬於 Bot）

回應：`{ "ok": true, "doc_id", "chunks": 42, "status": "pending|approved" }`

匯入流程：解析 → 切段（`chunk_size`／`chunk_overlap`）→ 段落類型標註 → 向量 →（選用）知識圖譜標註 → 待審核。只有 `approved` 的文件進檢索。

---

## `eval/run` — 自動評測（Python 版）

請求：`{ "bot_id", "flow_id", "bank_id", "question_ids": [], "repeat": 1, "settings_override": { … } }`
回應：`{ "ok": true, "run_id" }`；結果用 `GET /api/eval/runs/{run_id}` 取。

`settings_override` 讓評測套用試用設定而不改正式設定（改版把關用）。

---

## 設定 key

兩個後端讀同一組設定 key（見 `apps-script/Setup.gs` 的 `SETTINGS_DEFAULT` 與 `templates/*.json` 的 `settings`）。Python 版把它們存在 `bot_settings`（Flow 可用 `flow_settings` 覆蓋），key 一字不改；伺服器層級的東西（資料庫連線、模型供應商金鑰、CORS）用環境變數，不放進設定。

專案範本 JSON（`templates/`）格式兩邊通用：`settings`、`skills`、`synonyms`、`forbidden`、`referral`、`kgDefs`。
