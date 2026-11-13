# Python 版架構（多機器人）

這份文件定義 `server/` 要長什麼樣。先確認這份，再動手寫程式。

## 1. 目標

- 一台伺服器服務**多個機器人**，每個機器人有自己的設定、知識、Skill、紀錄、聊天頁入口、後台權限。
- 一個機器人可以有**多條流程**（例如「問答」與「現場問題收集」），流程由節點組成，開關決定走哪些節點。
- 設定語意、專案範本、聊天頁與 Apps Script 版相同；Apps Script 版的一份試算表 = 這裡的一個機器人。
- 可觀測：每一步的輸入輸出、耗時、token 都留紀錄，可接 Langfuse。
- 金鑰只在伺服器環境變數，前端與資料庫都不碰。

## 2. 名詞與階層

```
Workspace（工作區／租戶）
└── Bot（機器人）─── 一個聊天入口、一組設定、一組 Skill、自己的紀錄
    ├── Flow（流程）─── 一張 LangGraph 圖＋這條流程的開關；一個 Bot 至少一條
    ├── KnowledgeBase 連結 ─── Bot 可掛多個知識庫（知識庫屬於 Workspace，可被多個 Bot 共用）
    └── InviteCode、ChatLog、Feedback、EvalRun …
```

| 層 | 對應 Apps Script 版 | 說明 |
| --- | --- | --- |
| Workspace | （無，一份試算表就是全部） | 隔離單位：使用者、權限、知識庫、模型金鑰設定都掛在這裡。小型部署就一個 Workspace |
| Bot | 一份試算表＋一個部署 | 聊天頁用 `bot_id` 指定；設定 key 與 Apps Script 版一致 |
| Flow | 「版本」＋流程開關 | 預設每個 Bot 一條 `chat` 流程；之後可加 `collect`（收集）等其他圖 |
| KnowledgeBase | 「文件」＋「知識段落」工作表 | 拆出來是為了共用（例：「班級經營」與「兒童發展」兩個庫，一個 Bot 同時掛兩個、檢索時各取） |

**為什麼知識庫獨立於 Bot**：同一批核可文件常被多個機器人用；而且研究端要「兩類知識分開維護、回答時組合」，把知識庫當獨立物件最自然。Bot 掛知識庫時可以給每個庫一個標籤與配額（例如「發展」至少取 1 段）。

## 3. 元件

```
┌──────────────┐   HTTPS    ┌─────────────────────────────────────────────┐
│ web/chat/    │──────────▶│ FastAPI（app/）                              │
│ ?bot=<id>    │           │  /api/ui /api/check /api/chat /api/feedback  │
└──────────────┘           │  /api/admin/**（JWT，角色權限）               │
┌──────────────┐           │  /api/ingest  /api/eval/**                   │
│ 後台 SPA      │──────────▶│                                             │
│ web/admin/   │           │  graph/   LangGraph 圖（每條 Flow 一張）      │
└──────────────┘           │  services/ llm、embedding、ingest、kg、deid   │
                           │  settings/ Bot 設定快照、範本匯入            │
                           └──────┬───────────────┬──────────────┬───────┘
                                  │               │              │
                        PostgreSQL + pgvector   Gemini/OpenAI   Langfuse（選用）
                        （所有資料，含向量）      （模型 API）    （trace）
```

- **單一服務**：FastAPI 進程內跑 LangGraph，不另開 worker。文件匯入與評測這類長工作用背景任務（FastAPI `BackgroundTasks` 起步，量大再換 arq／Celery）。
- **資料庫一個**：所有表帶 `workspace_id`，Bot 相關的再帶 `bot_id`。向量直接在 `chunks.embedding`（pgvector，HNSW 索引）。
- **後台**是獨立的靜態 SPA（`web/admin/`），只打 `/api/admin/**`；跟聊天頁一樣部署在 GitHub Pages 或任何靜態空間。Apps Script 版的 `AdminUI.html` 功能照搬，介面重寫。
- **部署**：`docker-compose.yml` 兩個容器（`db`、`app`）。環境變數：`DATABASE_URL`、`GEMINI_API_KEY`、`OPENAI_API_KEY`、`JWT_SECRET`、`CORS_ORIGINS`、`LANGFUSE_*`（選用）。

## 4. 多機器人怎麼路由

- 聊天頁網址帶 `?bot=<bot_id>`（或子路徑 `/b/<slug>/`）；`config.js` 只填伺服器網址，不再填機器人。
- 每個 API 請求帶 `bot_id`；伺服器查 Bot → 載入該 Bot 的設定快照 → 跑該 Bot 的 `chat` 流程。
- 邀請碼屬於 Bot；關閉邀請碼時 `bot_id` 本身就是入口（搭配每日上限）。
- 後台登入後看到自己有權限的 Workspace 與 Bot 清單，切換 Bot 就切換所有分頁的資料。

## 5. 流程（Flow）

一條 Flow = 圖的名稱 + 這條流程專屬的開關值。第一版只有 `chat` 一種圖（節點見 `langgraph-mapping.md`）；「版本」（基本／多取向分流／全功能）仍是一鍵套開關的預設組合，存在 Flow 的設定裡。

為什麼現在就留 Flow 這層：
- 之後要把 Apps Script 的「現場問題收集」搬過來，它是另一張圖（題目 → 錄音或文字 → AI 追問 → 去識別化 → 存檔），掛在同一個 Bot 下共用設定與去識別化規則。
- 同一個 Bot 可以有「正式」與「試用」兩條 `chat` 流程（同一張圖、不同開關），評測時指定跑哪一條，做改版把關的並排比較。

Flow 的開關覆蓋 Bot 設定：讀設定時先 Bot、再 Flow，Flow 有值就蓋掉。

## 6. 權限

| 角色 | 能做 |
| --- | --- |
| `owner` | Workspace 全部，含成員管理、模型與金鑰相關設定 |
| `admin` | 某些 Bot 的全部設定、Skill、知識庫、評測 |
| `reviewer` | 知識庫審核（核可／停用）、圖譜候選審核、評測專家抽查；看不到設定與紀錄原文 |
| `analyst` | 只讀對話紀錄、統計、評測結果；可匯出 |

權限掛在「使用者 × Workspace」或「使用者 × Bot」。登入用帳號密碼＋JWT（之後可加 OIDC／AD）。

## 7. 資料表

與 `langgraph-mapping.md` 的最小集合相同，加上多機器人需要的幾張：

```
workspaces          id, name
users               id, login, pw_hash, display_name, disabled
memberships         user_id, workspace_id, bot_id (nullable=整個 workspace), role
bots                id, workspace_id, slug, name, created_at
flows               id, bot_id, kind('chat'|'collect'), name, is_default
bot_settings        bot_id, key, value            ← Apps Script 的「設定」表
flow_settings       flow_id, key, value           ← 覆蓋 bot_settings
knowledge_bases     id, workspace_id, name
bot_knowledge       bot_id, kb_id, label, min_hits, weight
documents           kb_id, …     chunks  kb_id, …, embedding vector(n)
skills / skill_history / synonyms / forbidden / referrals / invite_codes   皆帶 bot_id
kg_*                kb_id（圖譜跟著知識庫走）
conversations       bot_id, flow_id, session, turn, …   feedback  bot_id, …
eval_banks / eval_questions / eval_runs / eval_results   bot_id, flow_id
```

所有查詢都以 `bot_id`（或 `kb_id`）為第一個條件；跨 Bot 的只有 Workspace 層的統計。

## 8. 設定與範本

- 設定 key 一字不改沿用 Apps Script 版（`enable_triage`、`answer_schema`、`role_user`…）。預設值表（`SETTINGS_DEFAULT`）搬成 Python 的一份常數，兩邊同步維護。
- 專案範本 JSON 格式不變；多一個匯入目標：匯到哪個 Bot。範本裡沒有知識庫，知識庫另外建、另外掛。
- 設定快照：每次請求開始時把 Bot＋Flow 設定合併成一個 dict 放進 state，圖裡所有節點只讀 state。評測的 `settings_override` 在這一步合併進去。

## 9. 可觀測與紀錄

- 每個節點進出寫一筆 `trace`（與 Apps Script 的流程紀錄同格式），存在 `conversations.trace`，觀測邀請碼開啟時回給前端。
- 接 Langfuse：LangGraph 的 callback 直接送，不另外寫程式；沒設 `LANGFUSE_*` 就不送。
- 對話紀錄只存去識別化後的文字；`name_map` 不落地。

## 10. 與 Apps Script 版的差異（刻意的）

| | Apps Script | Python |
| --- | --- | --- |
| 機器人數 | 一份試算表一個 | 一台多個，Workspace 隔離 |
| 知識庫 | 綁在機器人上 | 獨立物件，可共用、可掛多個 |
| 流程 | 一條 | 每個 Bot 可多條 |
| 權限 | 單一層級 | 四種角色 |
| PDF 解析 | 模型讀 | `pypdf`／`pdfplumber`，掃描檔再 OCR |
| 向量 | 試算表 int8 | pgvector HNSW |
| 可觀測 | 自製流程面板 | 同格式 trace＋Langfuse |
| 長工作 | 分批＋觸發器 | 背景任務 |

## 11. 目錄

```
server/
├── app/
│   ├── main.py            FastAPI 建立、CORS、路由掛載
│   ├── routes/            chat.py admin_*.py ingest.py eval.py auth.py
│   ├── deps.py            取得 Bot、設定快照、目前使用者
│   └── schemas.py         Pydantic 請求／回應模型（對應 docs/api.md）
├── graph/
│   ├── state.py           ChatState
│   ├── nodes/             deid.py triage.py retrieve.py assemble.py generate.py check.py finish.py …
│   ├── chat_graph.py      組圖與條件邊
│   └── registry.py        flow kind → 圖
├── services/
│   ├── llm.py             Gemini／OpenAI 統一介面、JSON 模式、不相容參數重送
│   ├── embedding.py
│   ├── ingest.py          解析、切段、段落類型、向量、圖譜標註
│   ├── retrieval.py       向量＋關鍵字＋圖譜加分＋重排配額
│   ├── deid.py            規則、AI 清單套用
│   ├── kg.py
│   └── templates.py       範本匯入／匯出
├── settings/
│   ├── defaults.py        SETTINGS_DEFAULT、TIERS、TEXT_DEFAULTS（與 Apps Script 同步）
│   └── loader.py          Bot＋Flow 合併、{user}{subject}… 替換、schema 解析
├── db/
│   ├── models.py          SQLAlchemy
│   ├── session.py
│   └── migrations/        Alembic
├── tests/
├── pyproject.toml
├── .env.example
└── docker-compose.yml
```

## 12. 要你確認的決定

1. **Workspace 層要不要**：小型部署可以只有一個 Workspace，但表結構一開始就帶 `workspace_id`，之後不用改。建議保留。
2. **知識庫獨立於 Bot**：帶來「共用」與「多庫組合」，代價是後台多一個「知識庫」物件要管。建議做。
3. **Flow 層**：第一版只有 `chat` 一種圖，Flow 表先建、介面先只顯示一條。建議保留。
4. **後台做成獨立 SPA**（`web/admin/`）還是 FastAPI 吐 HTML：SPA 可以跟聊天頁一樣放靜態空間、跟後端分開部署；建議 SPA，用原生 JS 或輕量框架，不引入大型建置流程。
5. **向量維度固定在建庫時**：換向量模型＝重建知識庫，不做線上轉換。
6. **第一版不做**：OIDC／AD 登入、多語言、排程評測。表結構留位置。

確認後開始順序：`db/` → `settings/` → `services/llm.py` → `graph/` 節點 → `app/` 四支聊天端點 → 用 `web/chat/` 實測 → `ingest` → 後台 API → 後台 SPA → 評測。
