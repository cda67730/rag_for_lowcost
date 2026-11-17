# Python 版後端（開發中）

LangGraph + FastAPI + PostgreSQL/pgvector。一台伺服器服務多個機器人，每個機器人有自己的設定、Skill、紀錄與聊天入口；知識庫獨立、可以被多個機器人掛載。

> **狀態**：資料庫層、設定、專案範本、模型呼叫（Gemini／OpenAI）、去識別化、多知識庫檢索與重排、知識圖譜比對、回答檢查、問答流程（LangGraph）、聊天 API（`/api/ui`、`check`、`chat`、`feedback`）、文件匯入、後台 API（全部，見 `docs/admin-api.md`）與後台頁面（`web/admin/`）已完成並有測試。

## 文件

| 文件 | 內容 |
| --- | --- |
| [`INSTALL.md`](INSTALL.md) | **安裝前須知**：要先準備什麼、專案放哪裡、用啟動檔安裝的步驟、常見錯誤 |
| [`docs/architecture.md`](../docs/architecture.md) | 階層（Workspace → Bot → Flow、知識庫）、元件、權限、已確認的決定 |
| [`docs/data-model.md`](../docs/data-model.md) | 每張表的欄位、索引、刪除行為、向量查詢寫法 |
| [`docs/api.md`](../docs/api.md) | 聊天頁用的 API（與 Apps Script 版相同） |
| [`docs/admin-api.md`](../docs/admin-api.md) | 後台 API 與角色權限 |
| [`docs/ingest.md`](../docs/ingest.md) | 文件匯入：各格式解析、切段、段落類型、向量 |
| [`docs/langgraph-mapping.md`](../docs/langgraph-mapping.md) | 問答流程的節點、state，與 Apps Script 版的對照 |
| [`docs/settings.md`](../docs/settings.md) | 所有設定 key、預設值、存放位置 |

---

## 系統需求

- Docker 與 Docker Compose；或 Python 3.12／3.13＋PostgreSQL 16／17（含 pgvector，0.8 以上較好）。不用 Docker 的準備與步驟見 [`INSTALL.md`](INSTALL.md)
- Gemini 或 OpenAI 的 API 金鑰，至少一把
- 聊天頁與後台由伺服器直接提供（`/chat/`、`/admin/`），不用另外架網站；要放 GitHub Pages 也可以

## 環境變數

全部放在 `server/.env`（已在 `.gitignore`，不會進 repo）。範本在 `server/.env.example`。

| 變數 | 必要 | 說明 |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | 是 | 資料庫密碼；docker compose 用它建資料庫並自動組出 app 容器的連線字串 |
| `DATABASE_URL` | 不用 Docker 時要 | 直接跑 uvicorn／alembic／pytest 時用（不用 Docker 安裝、本機開發），例如 `postgresql+psycopg://rag:<密碼>@localhost:5432/rag`；compose 裡的 app 不讀這個 |
| `GEMINI_API_KEY` | 是 | Gemini 金鑰 |
| `OPENAI_API_KEY` | 否 | 要用 OpenAI 的生成或向量模型才填 |
| `JWT_SECRET` | 是 | 後台登入用，至少 32 個隨機字元（`openssl rand -hex 32`） |
| `CORS_ORIGINS` | 否 | 前端放在**別的網址**（例如 GitHub Pages）時才要設：允許呼叫 API 的前端網址，逗號分隔，例如 `https://<帳號>.github.io`。用伺服器自己的 `/chat/`、`/admin/` 不用設 |
| `SERVE_WEB` | 否 | 伺服器是否提供 `/chat/`（聊天頁）與 `/admin/`（後台），預設 `true`；前端只放 GitHub Pages 時可設 `false` |
| `WEB_DIR` | 否 | 前端檔案的資料夾；不設時自動找 Docker 映像檔裡的 `web/` 或 repo 的 `web/` |
| `PUBLIC_BASE_URL` | 否 | 伺服器對外網址，用在後台顯示聊天頁連結 |
| `CHAT_TIMEOUT_SECONDS` | 否 | 一個回合最多幾秒，預設 50；超過改給保守回答並記錄（品質旗標 `timeout`）。要比反向代理的逾時短 |
| `HOST` `PORT` | 否 | `start.sh`／`start.bat`／`run.py` 監聽的位址與埠，預設 `127.0.0.1`、`8000`。Docker 固定聽 `0.0.0.0`，埠預設 8000，雲端平台給 `PORT` 時照它的 |
| `WEB_CONCURRENCY` | 否 | 同時開幾個程式，預設 1；建議設成 CPU 核心數（2～4）。uvicorn 直接讀這個變數，Docker 與一般安裝都適用。背景工作（文件處理、評測、圖譜草稿）在接到請求的那個程式裡跑，伺服器重啟時沒做完的工作會標成失敗、可重試 |
| `DB_POOL_SIZE` `DB_MAX_OVERFLOW` `DB_POOL_TIMEOUT` | 否 | 資料庫連線池，預設 10／20／10 秒，每個程式各一份。連線只在讀寫時短暫持有，等模型時會先還回去。`WEB_CONCURRENCY` ×（POOL_SIZE＋MAX_OVERFLOW）要小於 PostgreSQL 的 `max_connections`（預設 100），例如 4 個程式就設 5／15 |
| `TRACE_RETENTION_DAYS` | 否 | 流程紀錄保留天數，預設 90；0＝不清。伺服器啟動時和之後每天清一次，只清流程紀錄，對話內容與統計保留。也可手動跑 `python -m app.cli purge-traces` |
| `LOG_LEVEL` | 否 | 伺服器 log 詳細程度：`debug`／`info`／`warning`／`error`，預設 `info`。uvicorn 本身的存取紀錄另用 `--log-level` 控制 |
| `APP_TIMEZONE` | 否 | 每日、每小時用量上限以哪個時區換日，預設 `Asia/Taipei` |
| `EXTERNAL_ALLOWED_HOSTS` | 用外部連線時要 | 機器人「外部連線」可以連的主機，逗號分隔，例如 `api.example.com,*.corp.example`。沒設＝外部連線一律不執行。後台改不了這份名單 |
| `EXT_SECRET_*` | 否 | 外部連線用的密碼，例如 `EXT_SECRET_CRM=...`；後台只填變數名稱。名稱一定要以 `EXT_SECRET_` 開頭，其他環境變數（模型金鑰、`JWT_SECRET`）不能被外部連線使用 |
| `EXTERNAL_ALLOW_HTTP` `EXTERNAL_ALLOW_PRIVATE` | 否 | 設成 `1` 才允許 http、才允許連內網位址。預設只允許 https、會拒絕內網與本機位址；只在測試時打開 |

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

> 第一次安裝建議照 [`INSTALL.md`](INSTALL.md) 一步步做，裡面有準備事項與常見錯誤。

Docker 不是必要的，需要的只有 Python 3.12 以上與 PostgreSQL 16／17（含 pgvector；0.8 以上較好，0.6 也能跑）。

1. 資料庫二選一：
   - 自己裝：Ubuntu `sudo apt install postgresql-16 postgresql-16-pgvector`，建資料庫與帳號（`createdb rag`）。
   - 雲端託管：Supabase、Neon 等內建 pgvector 的 PostgreSQL，取得連線字串即可。
2. 啟動（一個檔案搞定）：
   - Linux／macOS：`./start.sh`
   - Windows：在 `server` 資料夾裡雙擊 `start.bat`（或在命令提示字元執行）

   第一次執行會建立虛擬環境、照 `requirements.txt` 安裝套件，並從 `.env.example` 複製出 `.env` 後停下來；填好 `DATABASE_URL`、`JWT_SECRET`、模型金鑰（需要時再加 `CORS_ORIGINS`）再執行一次。之後每次啟動都會先自動更新資料庫結構（第一次會建立 vector 擴充，帳號要有建立擴充的權限），再依 `WEB_CONCURRENCY` 開程式。

   | 指令 | 用途 |
   |---|---|
   | `./start.sh`／`start.bat` | 正式執行 |
   | `./start.sh --dev`／`start.bat --dev` | 開發模式：改程式自動重啟、只開一個程式 |
   | `./start.sh --no-migrate` | 不自動更新資料庫結構 |

   監聽位址與埠在 `.env` 設 `HOST`（預設 `127.0.0.1`，只讓本機的反向代理連）與 `PORT`（預設 8000）。兩個腳本都只是準備好虛擬環境後執行 `python run.py`，已經有虛擬環境時也可以直接跑它。

   建第一個工作區與管理員（只要一次）：
   ```bash
   .venv/bin/python -m app.cli create-workspace --slug main --name "主要工作區" --owner admin      # Windows：.venv\Scripts\python -m app.cli ...
   ```
   伺服器、`app.cli` 指令、`alembic` 都會自動讀 `server/.env`；同名的環境變數已經有值時以環境變數為準。
3. 常駐：
   - Linux（systemd）：`ExecStart=/path/server/.venv/bin/python /path/server/run.py`、`WorkingDirectory=/path/server`、`Restart=on-failure`。
   - Windows：用 NSSM 之類的服務包裝工具，程式填 `C:\path\server\.venv\Scripts\python.exe`，參數填 `run.py`，啟動目錄填 `C:\path\server`。
4. 對外：前面放 Caddy 或 nginx 處理 HTTPS，反向代理到 `127.0.0.1:8000`。

#### Windows 注意事項

- **資料庫**：pgvector 官方沒有 Windows 安裝檔（要自己用 Visual Studio 編譯）。建議擇一：用 Docker Desktop 只跑資料庫（`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db`）、用 Supabase／Neon 等雲端 PostgreSQL，或把資料庫放在另一台 Linux。
- **Python**：到 python.org 裝 3.12 或 3.13（安裝時勾選 Add python.exe to PATH）。`start.bat` 會依序找 `py -3.13`、`py -3.12`、`python`；微軟商店的 `python` 捷徑會被略過。
- **中文編碼（cp950）**：繁中 Windows 的預設編碼是 cp950，第三方套件用它讀含中文的檔案會出現 `UnicodeDecodeError: 'cp950' codec can't decode…`。處理方式：
  - `run.py` 在 Windows 上一律以 UTF-8 模式（`python -X utf8`）執行，多開的子行程也是；`start.bat` 另外設了 `PYTHONUTF8=1`。
  - 會被套件用系統編碼讀的檔案（`alembic.ini`、`requirements*.txt`、`start.bat`）只用英文，測試會檢查。
  - `.env` 用記事本存成 UTF-8（含不含 BOM 都可以）；不小心存成 ANSI（Big5）也讀得到。
  - 自己手動跑 `alembic`、`pip`、`python -m app.cli` 時，先在命令提示字元執行 `set PYTHONUTF8=1`。
- **套件更新**：`git pull` 之後 `requirements.txt` 有變，`start.bat`／`start.sh` 會自動重裝；安裝中斷也會在下次重來。一直失敗時刪掉 `.venv` 資料夾再執行。
- **背景工作判斷**：Windows 上用系統 API 判斷舊的伺服器行程是否還在（不使用 `os.kill`，它在 Windows 上會直接結束行程）。

### 部署到 Railway

repo 不用改就能部署。照下面設定：

1. **資料庫**：在 Railway 專案裡新增有 pgvector 的 PostgreSQL。從範本（Template）搜尋 pgvector 選一個；Railway 預設的 PostgreSQL 不一定有 pgvector。
2. **伺服器**：新增服務 → 選這個 GitHub repo，再到服務的 Settings 設定：
   - **Root Directory**：留空。Railway 會自動用 repo 根目錄的 `Dockerfile` 建置（一起打包聊天頁與後台）；Build Logs 開頭應該是 Docker 建置，不是 Railpack。
   - **Pre-deploy Command**：`alembic upgrade head`（每次部署前更新資料庫結構）。
   - **Healthcheck Path**：`/healthz`。
   - **Networking → Generate Domain**：產生對外網址。伺服器會聽 Railway 給的 `PORT`。
3. **Variables**（伺服器服務的環境變數；不用 `.env` 檔）：

   | 變數 | 值 |
   |---|---|
   | `DATABASE_URL` | 引用資料庫服務的內部連線字串，例如 `${{pgvector.DATABASE_URL}}`（依你的資料庫服務名稱）。`postgresql://` 開頭也可以，程式會自動轉換 |
   | `JWT_SECRET` | 自己產生的 32 字元以上隨機字串 |
   | `GEMINI_API_KEY` 或 `OPENAI_API_KEY` | 模型金鑰 |
   | `FORWARDED_ALLOW_IPS` | `*`（只有 Railway 的代理連得到伺服器，這樣才拿得到使用者真正的 IP，邀請碼錯誤鎖定與次數限制才準） |
   | `WEB_CONCURRENCY` | 依方案的 CPU，通常 2 |

4. **建第一個管理員**：部署完成後，在本機裝好 Railway CLI 並 `railway link` 到這個專案，再執行（`-s` 後面是伺服器服務的名稱；會提示輸入 admin 的密碼）：
   ```bash
   railway ssh -s 伺服器服務名稱 -- python -m app.cli create-workspace --slug main --name "主要工作區" --owner admin
   ```
5. **開始使用**：後台是 `https://Railway給的網址/admin/`，聊天頁是 `https://Railway給的網址/chat/?bot=機器人代號`。同一個網址，不用設 `CORS_ORIGINS`，也不用改 `config.js`。

注意：
- 上傳的文件存在資料庫裡，不需要掛 Volume。
- 重新部署會中斷正在跑的背景工作（文件處理、評測），它們會在 15 分鐘內被標為失敗，到後台重試即可。部署盡量避開正在處理大量文件的時候。
- 資料庫連線數：`WEB_CONCURRENCY` ×（`DB_POOL_SIZE`＋`DB_MAX_OVERFLOW`）要小於資料庫的 `max_connections`。

### 套件版本

- `pyproject.toml` 寫套件的可用範圍；`requirements.txt`（執行用）和 `requirements-dev.txt`（加測試工具）固定成測試通過的確切版本，安裝與 Docker 都用這兩份。
- 升級套件：在新的虛擬環境 `pip install .` → 跑完測試 → `pip freeze --exclude rag-server > requirements.txt`（保留檔頭說明，並把 uvloop、tzdata 那兩行的平台條件加回去）。

### 本機開發（不用 Docker 跑 app）

```bash
cd server
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db   # 只開資料庫，開到本機 5432
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && pip install --no-deps -e .
./start.sh --dev                               # 自動更新資料庫結構後以開發模式啟動（Windows：start.bat --dev）
export TEST_DATABASE_URL=postgresql+psycopg://rag:<密碼>@localhost:5432/postgres
pytest                                         # 會自己建一個暫時資料庫跑遷移、測完刪掉；模型呼叫用假回應，不花 API 費用
pytest -m live                                 # 需要真金鑰的整合測試，預設不跑
```

---

## 第一次設定

1. 開後台：`https://伺服器網址/admin/`（本機是 <http://127.0.0.1:8000/admin/>），用剛建的 owner 帳號登入。聊天頁在 `/chat/?bot=機器人代號`。
   要把前端放在 GitHub Pages 等別的網址也可以：那邊的 `web/admin/config.js`、`web/chat/config.js` 填 API 網址，並把那個網址加進 `CORS_ORIGINS`。
2. **建知識庫**：Workspace → 知識庫 → 新增。選向量模型與維度（之後不能改）、可見範圍（整個 Workspace／只給指定 Bot）。向量模型可選 Gemini（`gemini-embedding-001`）或 OpenAI（`text-embedding-3-small`／`-large`），維度 768 或 1536；完整清單見 `docs/data-model.md`。同一個機器人掛的知識庫要用同一個向量模型。
3. **上傳文件**到知識庫 → 等處理完 → 看段落 → 核可。
4. **建機器人**：Workspace → 機器人 → 新增，填 slug（聊天頁網址會用到）與名稱，可直接選一份專案範本。
5. 進機器人 →「知識庫」分頁 → 掛載剛才的知識庫，設標籤與最少段數。
6. 「機器人設定」選版本、填角色用詞、選模型並測試。
7. 「邀請碼」新增一組，或關閉邀請碼（記得設每日上限）。
8. 「測試對話」試問，看流程紀錄。
9. 聊天頁：`web/chat/config.js` 設 `BACKEND: "server"`、`API_URL` 填 API 網址；使用者開 `https://<聊天頁網址>/?bot=<slug>`。

再開一個機器人：重複 4–8，可以掛同一個知識庫。

## API 與外部資料

讓其他系統呼叫機器人，或在回答前帶入其他系統的資料（會員、背景資料）。在後台機器人的「API 與外部資料」分頁設定。

- **API 金鑰**：每個機器人可以產生多把，明碼只在產生時顯示一次，資料庫只存雜湊。呼叫 `POST /api/v1/ask`，Header 帶 `Authorization: Bearer <金鑰>`，Body 帶 `message`、`session`、`history`（格式同聊天頁），以及可選的 `context`。
- **可帶入的欄位**：`context` 只收後台列出的欄位，其他欄位直接丟掉、不送給模型。
- **外部連線**：回答前即時呼叫其他系統（GET／POST，網址與參數可用 `{欄位}` 帶入呼叫端的值），取回 JSON 裡指定的欄位。主機要在 `EXTERNAL_ALLOWED_HOSTS` 裡；密碼放 `EXT_SECRET_*` 環境變數。
- **不保存外部資料**：帶入的欄位和外部連線查到的內容只在那一回合的記憶體裡組進提示詞，不寫資料庫、不寫日誌、不快取。對話紀錄的流程紀錄只記欄位名稱與連線狀態；回答照常存進對話紀錄。
- 紀錄的「單位」欄顯示「API：金鑰用途」；次數上限沿用每小時、每日上限。

| 回應 | 意思 |
| --- | --- |
| 200 | `{"ok": true, "type": ..., "answer": ..., "sources": ...}`，結構同聊天頁 |
| 401 | 金鑰無效或已停用 |
| 403 | 機器人已停用 |
| 422 | `message` 或 `session` 沒填、太長 |
| 429 | 超過次數上限 |
| 503 | 模型暫時無法使用 |

## 權限

| 角色 | 能做 |
| --- | --- |
| owner | Workspace 全部，含成員管理、建刪知識庫與機器人 |
| admin | 被指派的機器人的設定、Skill、邀請碼、紀錄、評測；指派在 Workspace 層的 admin 也能管知識庫 |
| reviewer | 審核文件、圖譜候選、評測專家抽查；看不到設定與對話原文 |
| analyst | 唯讀：紀錄、統計、評測結果，可匯出 |

成員在後台 Workspace → 成員 新增；一個人可以在不同機器人有不同角色，也可以加入多個工作區。

**總管理員**：負責建立、刪除工作區（後台右上角「系統管理」），也可以重設任何帳號的密碼；不會因此看到各工作區的內容。第一個用指令建立的工作區，它的 owner 會自動成為總管理員；從舊版升級時，最早建立的 owner 會被設為總管理員。增減總管理員：在「系統管理」的總管理員區塊設定或取消（帳號要先存在，至少保留一位），或在伺服器上用 `python -m app.cli superadmin --login 帳號 [--revoke]`。

**向量模型**：建知識庫時選，預設（建議）`gemini-embedding-001`・768 維。建立後不能改，要換只能新建知識庫並重新上傳所有文件；同一個機器人掛的知識庫必須用同一組，建議整個工作區統一。

**專家修正對話**：在機器人的「紀錄」點開一筆 →「修正並加入知識庫」，改好問題與回答後存成知識庫的新文件，可選處理完直接上線；列表會標「已修正」。要有工作區 admin／owner，或「工作區 reviewer＋這個機器人的 analyst」兩個角色。

**改自己的密碼**：後台右上角點自己的名字 →「我的帳號」。改完後其他裝置上的登入會被登出。

**帳號是全站共用的**：工作區 owner 只能重設「只屬於自己工作區」的帳號的密碼。帳號也屬於其他工作區時，請本人登入後自己改，或由總管理員重設（後台或 `python -m app.cli reset-password --login 帳號`），避免別的工作區的 owner 藉此登入你的帳號。

---

## 從 Apps Script 版搬過來

一份試算表 → 一個 Bot ＋ 一個同名知識庫。**只讀試算表、不會寫回**，Apps Script 版照常運作。

1. 在 Google 試算表選「檔案 › 下載 › Microsoft Excel (.xlsx)」。
2. 把檔案放到伺服器上執行：

```bash
docker compose exec app python -m app.cli import-apps-script \
  --workspace main --slug <bot-slug> --xlsx <下載的試算表.xlsx>
# 選項：--kb-slug <知識庫 slug>（預設同 bot）、--with-logs（一併搬對話紀錄與回饋）、--no-embed（先不算向量，之後在後台補算）
```

| 搬 | 不搬 |
| --- | --- |
| 設定（不合法的值略過並列出）、Skill 與版本紀錄、同義詞、禁止用語、轉介資源、邀請碼 | 管理員帳號（密碼格式不同；在後台「成員」重新建） |
| 文件與段落（含類型、讀者對象、適用年齡、情境）、知識圖譜（定義、節點、關係、來源、段落標註、候選）、評測題庫 | 評測結果 |
| 對話紀錄與回饋（要加 `--with-logs`） | 舊向量（依新知識庫的設定重算，會花一次向量 API 費用） |

- 搬過來的文件沒有原始檔，不能「重新處理」，但可以審核、改段落類型、重新分類、圖譜標註。
- 試算表裡有對話紀錄與邀請碼，匯入完請刪除這個 .xlsx。
- 同一個 slug 只能匯入一次；要重來請先在後台刪掉那個機器人與知識庫。

## 備份與更新

- 備份：`docker compose exec db pg_dump -Fc rag > backup_$(date +%F).dump`（含知識庫原始檔與向量）。還原用 `pg_restore`。
- 更新程式：`git pull` → `docker compose build app` → `docker compose up -d` → `docker compose exec app alembic upgrade head`。
- 資料庫遷移只往前；每次發版附遷移說明。

## 安全

- 聊天端點不需登入，但每個請求都要帶有效的 `bot_id`（＋邀請碼，若開啟）；有每日與每小時上限、邀請碼錯誤次數限制。
- 後台全部需要 JWT，依角色檢查；密碼用 argon2id；連續錯 5 次鎖 15 分鐘。
- 所有寫入寫稽核紀錄（誰、何時、改了什麼），不記金鑰與對話原文。
- 對話紀錄只存去識別化後的文字（`enable_deid` 開啟時）；原名對照表只在使用者瀏覽器。
- CORS 只允許 `CORS_ORIGINS` 列出的網址；伺服器自己提供的 `/chat/`、`/admin/` 是同一個網址，不經過 CORS。
- 伺服器提供的頁面加了 `X-Content-Type-Options: nosniff`；後台另加 `X-Frame-Options: DENY`（不讓別的網站嵌入），聊天頁可以嵌進其他網站。
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
├── docker-compose.yml       正式（映像檔用 repo 根目錄的 Dockerfile 建置）：db＋app，資料庫不對外
├── docker-compose.dev.yml   本機開發：把 db 開到 127.0.0.1:5432
└── .env.example
```
