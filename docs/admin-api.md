# 後台 API（Python 版）

後台 SPA（`web/admin/`）只打這些端點。聊天端點見 `api.md`；資料表見 `data-model.md`。

## 實作狀態

已完成（`server/app/routes/admin.py`）：§1 全部；§3 的 Bot 列表、建立、讀取、修改；§4 的設定讀寫與套用版本；§7 的知識庫列表、建立、讀取與 Bot 掛載；§8 的文件列表、上傳、讀取、審核、段落、原始檔、修復；§12 的查詢工作。`admin_bot.py`：§3 流程、§4 流程設定、§5 範本、§6 Skill（含版本與還原）、對照表、邀請碼。其餘端點開發中。

## 通則

- 前綴 `/api/admin`。請求與回應 JSON；上傳用 `multipart/form-data`。
- 認證：`Authorization: Bearer <JWT>`。JWT 內含 `sub`（user id）與 `tv`（token_version），有效 8 小時；`POST /auth/refresh` 換新。使用者 `token_version` 加一即讓舊 token 全部失效（改密碼、停用、強制登出）。
- 錯誤：HTTP 狀態碼＋`{ "error": "給人看的一句話", "code": "機器讀的代碼" }`。`401` 未登入／過期，`403` 沒權限，`404` 不存在或看不到（不區分，避免探測），`409` 衝突（例如知識庫被掛著不能刪），`422` 欄位驗證失敗（附 `fields`）。
- 列表一律分頁：`?limit=50&cursor=<上一頁回傳的 next>`，回應 `{ items: [], next: "…" | null }`。
- 所有寫入動作寫 `audit_log`。
- 路徑裡的 `{ws}`、`{bot}`、`{kb}` 用 id（uuid）；`slug` 只用在聊天頁。

### 角色

| 代號 | 角色 | 範圍 |
| --- | --- | --- |
| O | owner | Workspace 全部 |
| A | admin | 被指派的 Bot（或整個 Workspace）的設定、Skill、對照表、邀請碼、紀錄、評測；Workspace 層的 admin 也能管知識庫 |
| R | reviewer | 知識庫審核、圖譜候選審核、評測專家抽查 |
| N | analyst | 唯讀：紀錄、統計、評測結果，可匯出 |

下表「權限」欄列出可以呼叫的角色；O 一律可以，不另列。

---

## 1. 認證與自己

| 方法 | 路徑 | 權限 | 說明 |
| --- | --- | --- | --- |
| POST | `/auth/login` | 公開 | `{login, password}` → `{token, user}`。連續錯 5 次鎖 15 分鐘 |
| POST | `/auth/refresh` | 已登入 | 換新 token |
| POST | `/auth/logout` | 已登入 | 自己的 `token_version`+1 |
| GET | `/me` | 已登入 | 自己的資料、可存取的 Workspace 與 Bot 清單、各自角色 |
| POST | `/me/password` | 已登入 | `{old, new}`，成功後舊 token 失效 |

## 2. Workspace 與成員

| 方法 | 路徑 | 權限 | 說明 |
| --- | --- | --- | --- |
| GET | `/workspaces/{ws}` | 任一角色 | 名稱、統計摘要 |
| PATCH | `/workspaces/{ws}` | O | 改名 |
| GET | `/workspaces/{ws}/members` | O | 成員與角色 |
| POST | `/workspaces/{ws}/members` | O | `{login, display_name, password?, role, bot_id?}`；帳號不存在就建立 |
| PATCH | `/workspaces/{ws}/members/{membership}` | O | 改角色或範圍 |
| DELETE | `/workspaces/{ws}/members/{membership}` | O | 移除成員關係 |
| POST | `/workspaces/{ws}/users/{user}/reset-password` | O | 產生一次性密碼 |
| GET | `/workspaces/{ws}/audit` | O | 稽核紀錄，可依 bot／kb／action 篩選 |

建立 Workspace 與第一個 owner 用命令列（`python -m app.cli create-workspace`），不開 API。

## 3. 機器人與流程

| 方法 | 路徑 | 權限 | 說明 |
| --- | --- | --- | --- |
| GET | `/workspaces/{ws}/bots` | 任一 | 自己看得到的 Bot |
| POST | `/workspaces/{ws}/bots` | O、A（Workspace 層） | `{slug, name, template?}`；建立時自動建一條預設 `chat` 流程，可順便套範本 |
| GET | `/bots/{bot}` | 任一 | 基本資料、掛的知識庫、預設流程 |
| PATCH | `/bots/{bot}` | A | 改名、停用 |
| DELETE | `/bots/{bot}` | O | 刪除（連同紀錄，需二次確認參數 `?confirm=<slug>`） |
| GET | `/bots/{bot}/flows` | A、N | 流程清單 |
| POST | `/bots/{bot}/flows` | A | `{kind, name, copy_from?}`；第一版只接受 `kind=chat` |
| PATCH | `/flows/{flow}` | A | 改名、設為預設 |
| DELETE | `/flows/{flow}` | A | 不能刪預設流程 |

## 4. 設定

| 方法 | 路徑 | 權限 | 說明 | 對應 Apps Script |
| --- | --- | --- | --- | --- |
| GET | `/bots/{bot}/settings` | A | 每個 key 的值、預設、說明、是否用預設、是否被 Flow 覆蓋；附 `tiers`、`schema`（解析後的回答欄位） | `adminLoad` |
| PATCH | `/bots/{bot}/settings` | A | `{items:[{key,value}]}`；只接受已知 key，型別依預設值驗證 | `adminSaveSettings` |
| GET | `/flows/{flow}/settings` | A | 只列有覆蓋的 key | — |
| PATCH | `/flows/{flow}/settings` | A | 同上；`value: null` 表示移除覆蓋 | — |
| POST | `/bots/{bot}/tier` | A | `{tier, flow_id?}` 套用版本 | `adminApplyTier` |
| POST | `/bots/{bot}/describe` | A | `{kind, text, current?}` 用說的轉設定（回答欄位、意圖、檢查項目），回傳驗證過的結果，不存檔 | `adminDescribe` |
| POST | `/bots/{bot}/describe-back` | A | `{kind}` 把目前設定寫成描述 | `adminDescribeBack` |
| GET | `/models` | A | 依伺服器金鑰列出可用的生成與向量模型 | `adminModels` |
| POST | `/models/ping` | A | `{model}` 測試一次呼叫 | `adminPingModel` |

## 5. 專案範本

| 方法 | 路徑 | 權限 | 說明 | 對應 |
| --- | --- | --- | --- | --- |
| POST | `/bots/{bot}/template/apply` | A | 上傳範本 JSON，`replace` 參數同 Apps Script | `adminApplyTemplate` |
| GET | `/bots/{bot}/template/export` | A | 匯出目前 Bot 的設定、Skill、對照表、轉介為範本 JSON（不含知識庫、紀錄、邀請碼） | — |

## 6. Skill 與對照表

| 方法 | 路徑 | 權限 | 說明 | 對應 |
| --- | --- | --- | --- | --- |
| GET | `/bots/{bot}/skills` | A | | `adminLoad` |
| POST | `/bots/{bot}/skills` | A | 新增 | `adminSaveSkill` |
| PUT | `/skills/{skill}` | A | 整筆更新，版本+1 並寫 `skill_versions` | `adminSaveSkill` |
| GET | `/skills/{skill}/versions` | A | | `adminSkillHistory` |
| POST | `/skills/{skill}/restore` | A | `{version}` 還原成新版本 | — |
| DELETE | `/skills/{skill}` | A | | — |
| GET／PUT | `/bots/{bot}/synonyms` | A | 整份清單讀寫（量小，不逐筆） | 試算表直接編輯 |
| GET／PUT | `/bots/{bot}/forbidden` | A | 同上 | 同上 |
| GET／PUT | `/bots/{bot}/referrals` | A | 同上 | 同上 |
| GET | `/bots/{bot}/invite-codes` | A | | 同上 |
| POST | `/bots/{bot}/invite-codes` | A | `{unit, trace?, expires_at?, code?}`；不給 `code` 就隨機產生 | — |
| PATCH／DELETE | `/invite-codes/{id}` | A | 啟用／停用、觀測、刪除 | — |

## 7. 知識庫與掛載

| 方法 | 路徑 | 權限 | 說明 |
| --- | --- | --- | --- |
| GET | `/workspaces/{ws}/kbs` | 任一 | 知識庫清單；每筆附文件數、段落數、被哪些 Bot 掛著 |
| POST | `/workspaces/{ws}/kbs` | O、A（Workspace 層） | `{slug, name, embed_model, embed_dim, chunk_size, chunk_overlap, visibility}`；建立後向量設定不可改 |
| GET | `/kbs/{kb}` | 任一 | |
| PATCH | `/kbs/{kb}` | O、A（WS） | 名稱、說明、可見範圍、`auto_kg_tag`、切段參數（只影響之後上傳的文件） |
| PUT | `/kbs/{kb}/allowed-bots` | O、A（WS） | `{bot_ids:[]}`，`visibility=restricted` 的白名單 |
| DELETE | `/kbs/{kb}` | O | 被掛著回 409，附掛載的 Bot 清單 |
| GET | `/bots/{bot}/knowledge` | A | 這個 Bot 掛的庫與每筆的標籤、最少段數、權重、啟用 |
| GET | `/bots/{bot}/knowledge/available` | A | 可以掛的庫（同 Workspace、可見、向量模型相容） |
| PUT | `/bots/{bot}/knowledge` | A | 整份掛載清單 `{items:[{kb_id,label,min_hits,weight,enabled}]}`；違反可見範圍或向量模型不同回 422 |

## 8. 文件與段落

| 方法 | 路徑 | 權限 | 說明 | 對應 |
| --- | --- | --- | --- | --- |
| GET | `/kbs/{kb}/documents` | A（WS）、R | 依狀態篩選 | `adminLoad` |
| POST | `/kbs/{kb}/documents` | A（WS）、R | 上傳（multipart）或貼文字 `{title, text}`；回 `{doc_id, status:"processing"}`，背景處理 | `adminUploadDoc` |
| GET | `/documents/{doc}` | A（WS）、R | 含處理進度與錯誤 | — |
| PATCH | `/documents/{doc}` | A（WS）、R | 標題、出處、證據等級、狀態（`approved`／`disabled`）；核可時記錄審核者 | `adminSetDocStatus` |
| DELETE | `/documents/{doc}` | A（WS） | | — |
| GET | `/documents/{doc}/chunks` | A（WS）、R | 段落、類型、頁碼、圖譜標註 | `adminDocChunks` |
| PATCH | `/chunks/{chunk}` | A（WS）、R | 改段落類型 | `adminSetChunkType` |
| POST | `/documents/{doc}/classify` | A（WS） | 重新做段落類型標註 | `adminClassifyDoc` |
| POST | `/documents/{doc}/kg-tag` | A（WS） | 對這份文件做圖譜標註 | `adminMapDoc` |
| POST | `/kbs/{kb}/repair` | A（WS） | 檢查並補算缺少的向量 | `adminRepairVectors` |
| GET | `/documents/{doc}/file` | A（WS）、R | 下載原始檔 | — |

## 9. 知識圖譜

| 方法 | 路徑 | 權限 | 說明 | 對應 |
| --- | --- | --- | --- | --- |
| GET | `/kbs/{kb}/graph` | A（WS）、R、N | 節點、關係、定義、覆蓋度 | `adminGraphLoad` |
| POST | `/kbs/{kb}/graph/import` | A（WS） | 上傳 `knowledge-graph.json` | `adminImportKG` |
| PUT | `/kbs/{kb}/graph/defs` | A（WS） | 節點類型、關係、讀者對象 | — |
| GET | `/kbs/{kb}/graph/candidates` | A（WS）、R | | `adminGraphLoad` |
| POST | `/kg-candidates/{id}/action` | A（WS）、R | `{action: accept|merge|reject, target?}` | `adminCandAction` |
| POST | `/kbs/{kb}/graph/generate` | A（WS） | 依現有文件產生圖譜草稿，背景任務，回 `job_id` | `adminKgGenStart`／`Batch`／`Finish` |
| GET | `/kbs/{kb}/graph/export?format=json|ttl` | A（WS）、R、N | 下載圖譜或 Ontology | — |

## 10. 測試對話、紀錄、統計

| 方法 | 路徑 | 權限 | 說明 | 對應 |
| --- | --- | --- | --- | --- |
| POST | `/bots/{bot}/test-chat` | A | 與 `/api/chat` 相同請求，不需邀請碼，一律附 trace，可帶 `flow_id` 與 `settings_override` | `adminTestChat` |
| POST | `/bots/{bot}/flow-trial` | A | 流程控制台「試跑」：同上但只回走過的節點與耗時 | `adminFlowStats` |
| GET | `/bots/{bot}/flow-stats` | A、N | 流程控制台疊在節點上的運作數據（近 N 天） | `adminFlowStats` |
| GET | `/bots/{bot}/logs` | A、N | 篩選：期間、單位、回應類型、轉介、是否重寫、關鍵字；不回 trace | `adminLogs` |
| GET | `/logs/{id}` | A、N | 單筆完整紀錄含 trace | `adminLogDetail` |
| GET | `/bots/{bot}/stats` | A、N | 指標、知識缺口、常被引用文件、格式穩定度、L5 問題分類、回饋 | `adminLoad` 的統計部分 |
| GET | `/bots/{bot}/logs/export.csv` | A、N | | 問題統計 CSV |

reviewer 看不到紀錄原文（對話內容可能含敏感資訊）；analyst 看得到去識別化後的文字。

## 11. 評測

| 方法 | 路徑 | 權限 | 說明 | 對應 |
| --- | --- | --- | --- | --- |
| GET | `/bots/{bot}/eval/questions` | A、R、N | 篩選 kind／source／status | `adminEvalBank`、`adminEvalList` |
| POST | `/bots/{bot}/eval/questions` | A、R | 新增專家題或檢索題 | `adminEvalAddExpert`、`adminAddEval` |
| PATCH／DELETE | `/eval/questions/{id}` | A、R | 改狀態、專家確認、刪除 | `adminEvalSetQ`、`adminDeleteEval` |
| POST | `/bots/{bot}/eval/generate` | A | AI 出題（背景任務） | `adminEvalGenPlan`／`Batch`／`Special` |
| POST | `/bots/{bot}/eval/import-logs` | A | 從真實提問挑題 | `adminEvalImportLogs` |
| POST | `/bots/{bot}/eval/freeze` | A | 凍結目前 active 題目為新版本，回 `bank_version` | — |
| POST | `/bots/{bot}/eval/runs` | A | `{kind, flow_id?, bank_version?, repeat, settings_override}` 開始評測（背景） | `adminEvalRunStart`／`RunOne`、`adminRunEval` |
| GET | `/bots/{bot}/eval/runs` | A、R、N | | `adminEvalRuns` |
| GET | `/eval/runs/{run}` | A、R、N | 摘要與逐題結果 | `adminEvalSummary` |
| GET | `/eval/compare?a={run}&b={run}` | A、R、N | 兩次評測並排、差異、門檻判定 | — |
| POST | `/eval/results/{run}/{question}/{attempt}/review` | A、R | 專家抽查 | `adminEvalReview` |

## 12. 背景任務

長工作（文件處理、圖譜產生、AI 出題、評測）回傳 `job_id`：

| 方法 | 路徑 | 說明 |
| --- | --- | --- |
| GET | `/jobs/{job}` | `{status: queued|running|done|failed, progress: 0–1, message, result}` |
| POST | `/jobs/{job}/cancel` | 取消（已完成的部分保留） |

第一版用 FastAPI 背景任務＋`jobs` 表記錄狀態；伺服器重啟時 `running` 的工作標為 `failed`，前端可重試。

---

## 與 Apps Script 後台函式的完整對照

| Apps Script | Python |
| --- | --- |
| `adminLoad` | `GET /bots/{bot}`、`/settings`、`/skills`、`/kbs/{kb}/documents`、`/stats` 分開取 |
| `adminSaveSettings` | `PATCH /bots/{bot}/settings` |
| `adminApplyTier` | `POST /bots/{bot}/tier` |
| `adminApplyTemplate` | `POST /bots/{bot}/template/apply` |
| `adminDescribe`／`adminDescribeBack` | `POST /bots/{bot}/describe`／`describe-back` |
| `adminModels`／`adminPingModel` | `GET /models`／`POST /models/ping` |
| `adminSaveSkill`／`adminSkillHistory` | `POST`／`PUT /skills`、`GET /skills/{skill}/versions` |
| `adminUploadDoc` | `POST /kbs/{kb}/documents` |
| `adminSetDocStatus` | `PATCH /documents/{doc}` |
| `adminDocChunks`／`adminSetChunkType`／`adminClassifyDoc` | `GET /documents/{doc}/chunks`／`PATCH /chunks/{chunk}`／`POST /documents/{doc}/classify` |
| `adminRepairVectors` | `POST /kbs/{kb}/repair` |
| `adminMapDoc` | `POST /documents/{doc}/kg-tag` |
| `adminGraphLoad`／`adminImportKG`／`adminCandAction` | `GET /kbs/{kb}/graph`／`POST …/graph/import`／`POST /kg-candidates/{id}/action` |
| `adminKgGenStart`／`Batch`／`Finish` | `POST /kbs/{kb}/graph/generate`（一次呼叫，背景分批） |
| `adminTestChat` | `POST /bots/{bot}/test-chat` |
| `adminFlowStats` | `GET /bots/{bot}/flow-stats`、`POST /flow-trial` |
| `adminLogs`／`adminLogDetail` | `GET /bots/{bot}/logs`／`GET /logs/{id}` |
| `adminAddEval`／`adminDeleteEval`／`adminRunEval` | 檢索題：`eval/questions`（`kind=retrieval`）、`eval/runs` |
| `adminEvalBank`／`adminEvalList`／`adminEvalSetQ`／`adminEvalAddExpert` | `eval/questions` 系列 |
| `adminEvalGenPlan`／`Batch`／`Special` | `POST /bots/{bot}/eval/generate` |
| `adminEvalImportLogs` | `POST /bots/{bot}/eval/import-logs` |
| `adminEvalRunStart`／`RunOne` | `POST /bots/{bot}/eval/runs`（背景逐題） |
| `adminEvalRuns`／`adminEvalSummary`／`adminEvalReview` | `GET eval/runs`、`GET /eval/runs/{run}`、`POST …/review` |
