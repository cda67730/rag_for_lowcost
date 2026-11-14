# Python 版後端（開發中）

LangGraph + FastAPI + PostgreSQL/pgvector。一台伺服器服務多個機器人，每個機器人有自己的設定、Skill、紀錄與聊天入口；知識庫獨立、可以被多個機器人掛載。

> **狀態**：資料庫層、設定、專案範本、模型呼叫（Gemini／OpenAI）、去識別化、多知識庫檢索與重排、知識圖譜比對、回答檢查、問答流程（LangGraph）、聊天 API（`/api/ui`、`check`、`chat`、`feedback`）、文件匯入、後台 API 第一批（登入、機器人、設定、知識庫與掛載、文件上傳與審核、背景任務）已完成並有測試；後台其餘 API 與後台頁面開發中。下面標「規劃」的指令尚未實作。

## 文件

| 文件 | 內容 |
| --- | --- |
| [`docs/architecture.md`](../docs/architecture.md) | 階層（Workspace → Bot → Flow、知識庫）、元件、權限、已確認的決定 |
| [`docs/data-model.md`](../docs/data-model.md) | 每張表的欄位、索引、刪除行為、向量查詢寫法 |
| [`docs/api.md`](../docs/api.md) | 聊天頁用的 API（與 Apps Script 版相同） |
| [`docs/admin-api.md`](../docs/admin-api.md) | 後台 API 與角色權限 |
| [`docs/ingest.md`](../docs/ingest.md) | 文件匯入：各格式解析、切段、段落類型、向量 |
| [`docs/langgraph-mapping.md`](../docs/langgraph-mapping.md) | 問答流程的節點、state，與 Apps Script 版的對照 |
| [`docs/settings.md`](../docs/settings.md) | 所有設定 key、預設值、存放位置 |

---

## 系統需求

- Docker 與 Docker Compose（正式環境建議）；或 Python 3.12＋PostgreSQL 17（含 pgvector 0.8 以上）
- Gemini API 金鑰（必要）；OpenAI API 金鑰（選用）
- 一個能放靜態網頁的地方給聊天頁與後台（GitHub Pages 即可）

## 環境變數

全部放在 `server/.env`（已在 `.gitignore`，不會進 repo）。範本在 `server/.env.example`。

| 變數 | 必要 | 說明 |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | 是 | 資料庫密碼；docker compose 用它建資料庫並自動組出 app 容器的連線字串 |
| `DATABASE_URL` | 不用 Docker 時要 | 直接跑 uvicorn／alembic／pytest 時用（不用 Docker 安裝、本機開發），例如 `postgresql+psycopg://rag:<密碼>@localhost:5432/rag`；compose 裡的 app 不讀這個 |
| `GEMINI_API_KEY` | 是 | Gemini 金鑰 |
| `OPENAI_API_KEY` | 否 | 要用 OpenAI 的生成或向量模型才填 |
| `JWT_SECRET` | 是 | 後台登入用，至少 32 個隨機字元（`openssl rand -hex 32`） |
| `CORS_ORIGINS` | 是 | 允許呼叫 API 的前端網址，逗號分隔，例如 `https://<帳號>.github.io` |
| `PUBLIC_BASE_URL` | 否 | 伺服器對外網址，用在後台顯示聊天頁連結 |
| `TRACE_RETENTION_DAYS` | 否 | 流程紀錄保留天數，預設 90 |
| `LANGFUSE_HOST` `LANGFUSE_PUBLIC_KEY` `LANGFUSE_SECRET_KEY` | 否 | 三個都有才會送 trace 到 Langfuse |
| `LOG_LEVEL` | 否 | 預設 `info` |
| `APP_TIMEZONE` | 否 | 每日、每小時用量上限以哪個時區換日，預設 `Asia/Taipei` |

### API 金鑰放哪裡

| 位置 | 可以嗎 |
| --- | --- |
| `server/.env`（或部署平台的 secret／環境變數設定） | **唯一正確的位置** |
| 程式碼、`docker-compose.yml`、Dockerfile | 不行（會進 repo 與映像檔） |
| 資料庫（設定表、任何欄位） | 不行（有資料庫讀取權的人都看得到，也會進備份） |
| 前端 `web/` 任何檔案 | 不行（公開） |
| 專案範本 JSON、匯出檔、截圖、對話紀錄 | 不行 |

- 金鑰只在伺服器進程記憶體裡，不寫 log、不出現在錯誤訊息、不回傳給任何 API。
- 換金鑰：改 `.env` → `docker compose up -d`（重啟 app 容器即可，不用重建資料庫）。
- 懷疑外洩：先到供應商後台作廢，再換新的。
- 建議在 Google Cloud／OpenAI 後台設用量上限；每個 Bot 另有 `daily_limit`、`hourly_limit`。

---

## 安裝（Docker）

```bash
git clone <repo> && cd <repo>/server
cp .env.example .env          # 填入上表的變數
docker compose up -d          # 啟動 db（Postgres 17 + pgvector）與 app
docker compose exec app alembic upgrade head          # 建立資料表
docker compose exec app python -m app.cli create-workspace --slug main --name "主要工作區" --owner admin
# 會提示輸入 owner 的密碼
```

API 在 `http://localhost:8000`，健康檢查 `GET /healthz`：資料庫連得上、pgvector 已安裝、遷移已跑過才回 200（遷移前回 503 是正常的）。資料庫埠不對外開放。對外服務時在前面放反向代理（Caddy／nginx）處理 HTTPS，並在 `.env` 設 `FORWARDED_ALLOW_IPS=<代理的 IP>`（映像檔已開 `--proxy-headers`），邀請碼錯誤次數才會依使用者的真實 IP 計算。

### 不用 Docker 安裝（正式環境也可以）

Docker 不是必要的，需要的只有 Python 3.12 以上與 PostgreSQL 16／17（含 pgvector；0.8 以上較好，0.6 也能跑）。

1. 資料庫二選一：
   - 自己裝：Ubuntu `sudo apt install postgresql-16 postgresql-16-pgvector`，建資料庫與帳號（`createdb rag`）。
   - 雲端託管：Supabase、Neon 等內建 pgvector 的 PostgreSQL，取得連線字串即可。
2. 程式：
   ```bash
   cd server
   python3 -m venv .venv && source .venv/bin/activate
   pip install -e .
   cp .env.example .env     # 填 DATABASE_URL、GEMINI_API_KEY、JWT_SECRET、CORS_ORIGINS
   alembic upgrade head     # 會自動建立 vector 擴充與資料表（帳號要有建立擴充的權限）
   python -m app.cli create-workspace --slug main --name "主要工作區" --owner admin
   uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
   ```
3. 常駐：Linux 用 systemd（`ExecStart=/path/server/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers`、`WorkingDirectory=/path/server`、`EnvironmentFile=/path/server/.env`）；Windows 用 NSSM 之類的服務包裝工具。
4. 對外：前面放 Caddy 或 nginx 處理 HTTPS，反向代理到 `127.0.0.1:8000`。

### 本機開發（不用 Docker 跑 app）

```bash
cd server
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db   # 只開資料庫，開到本機 5432
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
export TEST_DATABASE_URL=postgresql+psycopg://rag:<密碼>@localhost:5432/postgres
pytest                                         # 會自己建一個暫時資料庫跑遷移、測完刪掉；模型呼叫用假回應，不花 API 費用
pytest -m live                                 # 需要真金鑰的整合測試，預設不跑
```

---

## 第一次設定

1. 開後台（`web/admin/`，部署方式同聊天頁），`config.js` 填 API 網址，用剛建的 owner 帳號登入。
2. **建知識庫**：Workspace → 知識庫 → 新增。選向量模型與維度（之後不能改）、可見範圍（整個 Workspace／只給指定 Bot）。向量模型可選 Gemini（`gemini-embedding-001`）或 OpenAI（`text-embedding-3-small`／`-large`），維度 768 或 1536；完整清單見 `docs/data-model.md`。同一個機器人掛的知識庫要用同一個向量模型。
3. **上傳文件**到知識庫 → 等處理完 → 看段落 → 核可。
4. **建機器人**：Workspace → 機器人 → 新增，填 slug（聊天頁網址會用到）與名稱，可直接選一份專案範本。
5. 進機器人 →「知識庫」分頁 → 掛載剛才的知識庫，設標籤與最少段數。
6. 「機器人設定」選版本、填角色用詞、選模型並測試。
7. 「邀請碼」新增一組，或關閉邀請碼（記得設每日上限）。
8. 「測試對話」試問，看流程紀錄。
9. 聊天頁：`web/chat/config.js` 設 `BACKEND: "server"`、`API_URL` 填 API 網址；使用者開 `https://<聊天頁網址>/?bot=<slug>`。

再開一個機器人：重複 4–8，可以掛同一個知識庫。

## 權限

| 角色 | 能做 |
| --- | --- |
| owner | Workspace 全部，含成員管理、建刪知識庫與機器人 |
| admin | 被指派的機器人的設定、Skill、邀請碼、紀錄、評測；指派在 Workspace 層的 admin 也能管知識庫 |
| reviewer | 審核文件、圖譜候選、評測專家抽查；看不到設定與對話原文 |
| analyst | 唯讀：紀錄、統計、評測結果，可匯出 |

成員在後台 Workspace → 成員 新增；一個人可以在不同機器人有不同角色。

---

## 從 Apps Script 版搬過來

一份試算表 → 一個 Bot ＋ 一個同名知識庫：

```bash
docker compose exec app python -m app.cli import-apps-script \   # 規劃
  --workspace main --slug <bot-slug> --xlsx <下載的試算表.xlsx>
```

搬設定、Skill、同義詞、禁止用語、轉介資源、邀請碼、文件與段落文字、圖譜、評測題庫；向量依新知識庫的設定重算（會花一次向量 API 費用）。對話紀錄可選擇是否搬（`--with-logs`）。

## 備份與更新

- 備份：`docker compose exec db pg_dump -Fc rag > backup_$(date +%F).dump`（含知識庫原始檔與向量）。還原用 `pg_restore`。
- 更新程式：`git pull` → `docker compose build app` → `docker compose up -d` → `docker compose exec app alembic upgrade head`。
- 資料庫遷移只往前；每次發版附遷移說明。

## 安全

- 聊天端點不需登入，但每個請求都要帶有效的 `bot_id`（＋邀請碼，若開啟）；有每日與每小時上限、邀請碼錯誤次數限制。
- 後台全部需要 JWT，依角色檢查；密碼用 argon2id；連續錯 5 次鎖 15 分鐘。
- 所有寫入寫稽核紀錄（誰、何時、改了什麼），不記金鑰與對話原文。
- 對話紀錄只存去識別化後的文字（`enable_deid` 開啟時）；原名對照表只在使用者瀏覽器。
- CORS 只允許 `CORS_ORIGINS` 列出的網址。
- 上傳檔案依檔頭判斷類型、限制大小，解析在背景任務中執行，不執行檔案內的巨集或程式。

## 目錄

```
server/
├── app/          FastAPI：main.py、routes/、deps.py、schemas.py、cli.py
├── graph/        LangGraph：state.py、nodes/、chat_graph.py、registry.py
├── services/     llm、embedding、ingest、retrieval、deid、kg、templates
├── settings/     defaults.py（與 apps-script/Setup.gs 同步）、loader.py
├── db/           models.py、session.py、migrations/
├── tests/        pytest；需要 TEST_DATABASE_URL
├── alembic.ini
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml       正式：db＋app，資料庫不對外
├── docker-compose.dev.yml   本機開發：把 db 開到 127.0.0.1:5432
└── .env.example
```
