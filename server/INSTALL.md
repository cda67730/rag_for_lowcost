# 安裝前須知

不用 Docker、用啟動檔（`start.bat`／`start.sh`）安裝時，照這份做。要用 Docker 的話看 [README 的「安裝（Docker）」](README.md#安裝docker)。

---

## 一、先準備好四樣東西

### 1. Python 3.12 或 3.13

- **Windows**：到 [python.org](https://www.python.org/downloads/) 下載安裝。安裝畫面第一頁記得勾 **Add python.exe to PATH**。
- **Linux／macOS**：先用 `python3 --version` 看版本。不到 3.12 的話，Ubuntu 用 `apt`、Mac 用 Homebrew 裝新版。

### 2. 裝好 pgvector 的 PostgreSQL

三種方式選一種：

| 方式 | 做法 | 適合 |
|---|---|---|
| 雲端 | 到 Supabase 或 Neon 開一個資料庫，複製它給的連線字串 | 最省事，Windows 也建議用這個 |
| Docker 只跑資料庫 | 裝 Docker Desktop，在 `server` 資料夾執行 `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db` | 自己電腦測試 |
| 自己安裝（Linux） | `sudo apt install postgresql-16 postgresql-16-pgvector`，再 `createdb rag` | 正式主機 |

- Windows 不建議直接裝 PostgreSQL：pgvector 沒有官方的 Windows 安裝檔，要自己編譯。
- 資料庫帳號要有**建立擴充**（`CREATE EXTENSION`）的權限。第一次啟動時會自動建立 vector 擴充和所有資料表，不用自己跑 SQL。
- 版本：PostgreSQL 16 或 17；pgvector 0.8 以上較好，0.6 也能跑。

#### Windows：裝好 Docker Desktop 之後怎麼開資料庫

不用另外安裝 PostgreSQL。專案的 `docker-compose.yml` 已經指定好含 pgvector 的映像檔（`pgvector/pgvector:pg17`），一個指令就會下載並啟動。

1. **啟動 Docker Desktop**，等左下角顯示 Engine running。第一次安裝時，它可能要求啟用 WSL 2 並重開機，照畫面做就好。
2. **準備 `server\.env`**：先執行一次 `start.bat`，它會從 `.env.example` 建出 `.env`。用記事本打開，改這兩行，**兩個密碼要一樣**：
   ```
   POSTGRES_PASSWORD=自己取的密碼
   DATABASE_URL=postgresql+psycopg://rag:自己取的密碼@127.0.0.1:5432/rag
   ```
   密碼只用英文字母和數字比較保險。`@ : / ? #` 這些符號放進連線字串要另外轉碼，容易出錯。
3. **開資料庫**：在 `server` 資料夾開命令提示字元，執行：
   ```
   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db
   ```
   第一次會下載映像檔，大約幾百 MB。
4. **確認狀態**：執行 `docker compose ps`，db 那一列顯示 `healthy` 就好了。
5. **回到下面「三、安裝步驟」的第 3 步**，再執行一次 `start.bat`。

之後要注意的事：
- **開機自動啟動**：資料庫設定了 `restart: unless-stopped`，只要 Docker Desktop 有開，資料庫就會跟著啟動。到 Docker Desktop 的設定勾「Start Docker Desktop when you sign in」，開機後就不用手動開。
- **資料存在哪**：資料存在 Docker 的資料卷（volume）`pgdata`，關掉、重開機都不會不見。**不要執行 `docker compose down -v`**，`-v` 會連資料一起刪掉。
- **密碼只在第一次建立資料庫時生效**。之後再改 `POSTGRES_PASSWORD`，資料庫的密碼不會跟著變，`DATABASE_URL` 要繼續用原本的密碼。真的要換密碼，要進資料庫用 `ALTER USER` 改。
- **5432 埠被佔用**：電腦上已經裝過 PostgreSQL 的話，5432 埠可能被佔用。可以先停掉原本的 PostgreSQL 服務，或改 `docker-compose.dev.yml` 的埠，例如 `127.0.0.1:5433:5432`，`DATABASE_URL` 也改成 `:5433`。
- **只開放給本機**：資料庫只開放給本機（127.0.0.1）連線，區網裡的其他電腦連不到，這是刻意的。

### 3. 模型 API 金鑰

Gemini（`GEMINI_API_KEY`）或 OpenAI（`OPENAI_API_KEY`），至少一把。金鑰只放在伺服器的 `.env`，不要放進前端、資料庫或 repo。

### 4. 一組 JWT_SECRET

至少 32 個字元的隨機字串，用來簽後台的登入憑證。用這行產生：

```
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## 二、專案放哪個資料夾

- 放哪裡都可以，例如 Windows `C:\rag_for_lowcost`、Linux `/opt/rag_for_lowcost`。用 `git clone` 或從 GitHub 下載 zip 解壓縮都行，路徑有空白或中文也沒關係。
- 啟動檔會自動切到它所在的 `server` 資料夾，雙擊、從別的資料夾執行、設成服務都一樣。虛擬環境（`.venv`）和設定檔（`.env`）都放在 `server` 底下。
- **不要放在 OneDrive、Dropbox 等會同步的資料夾**：`.venv` 有上萬個小檔案，同步時容易鎖檔、造成安裝失敗。
- **整個專案搬到別的位置後，要刪掉 `server\.venv`** 再執行啟動檔。虛擬環境記得原本的路徑，搬家後就不能用了；啟動檔會自動重建。

---

## 三、安裝步驟

1. **第一次執行啟動檔**
   - Windows：在 `server` 資料夾裡雙擊 `start.bat`
   - Linux／macOS：`cd server && ./start.sh`

   它會建立虛擬環境、安裝套件（幾分鐘），從 `.env.example` 複製出 `server/.env`，然後停下來。

2. **填 `server/.env`**：用記事本或任何編輯器打開，至少填這三項：

   ```
   DATABASE_URL=postgresql+psycopg://帳號:密碼@主機:5432/資料庫名稱
   JWT_SECRET=上面產生的那串
   GEMINI_API_KEY=你的金鑰          # 或 OPENAI_API_KEY
   ```

   - 雲端資料庫給的連線字串（開頭是 `postgres://` 或 `postgresql://`）可以直接貼上，程式會自動轉成需要的格式。
   - 記事本存檔時編碼選 UTF-8（預設就是）。

3. **再執行一次啟動檔**：會先檢查設定、建立資料表，再啟動伺服器。看到 `啟動：http://127.0.0.1:8000` 就成功了。用瀏覽器開 <http://127.0.0.1:8000/healthz>，顯示 `"ok":true` 表示伺服器和資料庫都正常。

4. **建第一個工作區和管理員**（只要做一次）。伺服器開著，另開一個命令視窗，進 `server` 資料夾執行：

   - Windows：
     ```
     set PYTHONUTF8=1
     .venv\Scripts\python -m app.cli create-workspace --slug main --name "主要工作區" --owner admin
     ```
   - Linux／macOS：
     ```
     .venv/bin/python -m app.cli create-workspace --slug main --name "主要工作區" --owner admin
     ```

   會要你輸入 admin 的密碼（至少 10 個字元，輸入時畫面不會顯示）。
   這個帳號同時是**總管理員**：之後要再開工作區，直接在後台右上角「系統管理」建立，不用再下指令。忘記密碼時，在伺服器上執行 `python -m app.cli reset-password --login admin`。

5. **開後台**
   - 伺服器直接提供後台與聊天頁：瀏覽器開 <http://127.0.0.1:8000/admin/>，用 admin 登入。聊天頁是 <http://127.0.0.1:8000/chat/?bot=機器人代號>。
   - 不用改 `config.js`、不用設 `CORS_ORIGINS`（同一個網址）。前端要另外放在 GitHub Pages 時才需要設定，見 README。

接下來建知識庫、機器人、邀請碼，見 [README 的「第一次設定」](README.md#第一次設定)。

---

## 四、之後每次啟動

- 執行同一個啟動檔就好。它會自動更新資料庫結構；`requirements.txt` 有變（例如 `git pull` 之後）也會自動重裝套件。
- 開發時用 `start.bat --dev`／`./start.sh --dev`：改程式會自動重啟。
- 監聽位址與埠在 `.env` 的 `HOST`、`PORT` 設定，預設 `127.0.0.1:8000`。
- 要開機自動啟動，見 README 的「常駐」：Linux 用 systemd，Windows 用 NSSM。

---

## 五、正式給別人用之前

- 前面放 Caddy 或 nginx 處理 HTTPS，反向代理到 `127.0.0.1:8000`。
- `.env` 設 `FORWARDED_ALLOW_IPS` 為反向代理的 IP。沒設的話，系統會把所有人當成同一個 IP，邀請碼錯誤鎖定、次數限制都會失準。
- 前端用伺服器自己的 `/chat/`、`/admin/` 時，`CORS_ORIGINS` 留空；前端另外放在別的網址時，只留那些正式網址。
- `WEB_CONCURRENCY` 設成 CPU 核心數（2～4）。程式數 ×（`DB_POOL_SIZE`＋`DB_MAX_OVERFLOW`）要小於 PostgreSQL 的 `max_connections`（預設 100）。
- 定期備份資料庫，見 README 的「備份與更新」。

---

## 六、常見錯誤

| 看到的訊息 | 原因與處理 |
|---|---|
| `Python 3.12 or later is required` | 沒裝 Python 3.12 以上，或安裝時沒勾 Add python.exe to PATH。重新安裝並勾選。 |
| `Installation failed` | 多半是網路或公司 proxy 擋住 pip。確認能連 pypi.org 後再執行；一直失敗就刪掉 `.venv` 重來。 |
| `server/.env 還沒填：DATABASE_URL` | `.env` 的必填欄位是空的，填好再執行。 |
| `JWT_SECRET 至少要 32 個字元` | 用上面的指令重新產生一組。 |
| `connection refused`、`could not connect to server` | 資料庫沒開，或 `DATABASE_URL` 的主機、埠、帳密不對。用 Docker 跑資料庫的話，先確認 Docker Desktop 有開、`docker compose ps` 顯示 healthy。 |
| `password authentication failed for user "rag"` | `DATABASE_URL` 的密碼和建立資料庫時的 `POSTGRES_PASSWORD` 不一樣。注意：資料庫建好之後再改 `POSTGRES_PASSWORD` 不會生效。 |
| `env file …\.env not found`（執行 docker compose 時） | `server` 資料夾裡還沒有 `.env`。先執行一次 `start.bat`，或把 `.env.example` 複製成 `.env`。 |
| `permission denied to create extension "vector"` | 資料庫帳號沒有建立擴充的權限。請管理員先執行 `CREATE EXTENSION vector;`，或改用有權限的帳號。 |
| `extension "vector" is not available` | 資料庫沒有安裝 pgvector。換用雲端或 Docker 的資料庫，或在主機上安裝 pgvector。 |
| `'cp950' codec can't decode…` | 繁中 Windows 的編碼問題。用啟動檔執行就會自動處理；手動跑 `pip`、`alembic`、`python -m app.cli` 前先執行 `set PYTHONUTF8=1`。 |
| `address already in use`、`WinError 10048` | 8000 埠被別的程式佔用。在 `.env` 把 `PORT` 改成別的數字。 |
| 後台登入時顯示連線失敗 | 用伺服器的 `/admin/` 時，確認伺服器有在跑。後台放在別的網址時：`web/admin/config.js` 的 `API_URL` 不對，或 `CORS_ORIGINS` 沒包含後台的網址。改完 `.env` 要重新啟動伺服器。 |
| 問答一直回保守回答 | 模型金鑰沒填或填錯，看伺服器視窗裡的錯誤訊息。 |
