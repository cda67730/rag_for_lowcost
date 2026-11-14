# 低成本 RAG 知識問答系統

依核可的知識庫回答、附出處、有檢查關卡的問答機器人。一套設定語意、一套聊天頁、一套專案範本，兩個可互換的後端：

| | `apps-script/` | `server/` |
| --- | --- | --- |
| 技術 | Google 試算表 + Apps Script | Python + LangGraph + FastAPI + PostgreSQL/pgvector |
| 成本 | 只有模型 API 用量費 | 需要一台能跑 Docker 的機器 |
| 適合 | 單一機器人、知識庫數千段以內、不想養伺服器 | 一台多機器人、知識庫共用、多條流程、權限分級、大知識庫、可觀測 |
| 狀態 | **參考實作，功能凍結，只修錯** | **開發中** |

兩個後端對外是同一份 [API 合約](docs/api.md)，`web/chat/` 的聊天頁改一行網址就能切換。

```
rag_for_lowcost/
├── apps-script/   Apps Script 版後端（含安裝說明 README）
├── server/        Python 版後端（開發中）
├── web/chat/      聊天頁，GitHub Pages 部署，兩版共用
├── templates/     專案範本 JSON，兩版共用（一個領域一份：設定、Skill、同義詞、禁止用語、轉介、圖譜定義）
├── docs/          規格文件（見下方索引）
├── tools/         gen-settings-doc.js：由 Setup.gs 產生 docs/settings.md
├── releases/      打包 zip
└── sync-trace.js  把 web/chat 的共用程式同步成 apps-script 的 HTML
```

## 從哪裡開始

- **要馬上用**：看 [`apps-script/README.md`](apps-script/README.md)。有安裝腳本也有手動步驟；API 金鑰怎麼放、怎麼更新、怎麼再開一個機器人都在裡面。
- **要部署或開發 Python 版**：看 [`server/README.md`](server/README.md)（安裝、環境變數、金鑰、權限、備份），再依需要讀下面的規格。

## 文件索引

| 文件 | 給誰 | 內容 |
| --- | --- | --- |
| [`apps-script/README.md`](apps-script/README.md) | 使用 Apps Script 版的人 | 安裝、金鑰、更新、功能說明、常見問題 |
| [`server/README.md`](server/README.md) | 部署 Python 版的人 | 安裝、環境變數、金鑰、第一次設定、權限、搬遷、備份、安全 |
| [`docs/architecture.md`](docs/architecture.md) | 開發者 | 多機器人階層、元件、路由、權限、已確認的決定 |
| [`docs/data-model.md`](docs/data-model.md) | 開發者 | 資料表定義、索引、刪除行為、向量查詢、與試算表對照 |
| [`docs/api.md`](docs/api.md) | 前端／兩版後端 | 聊天頁依賴的 API（兩版共用）、前端如何切換後端 |
| [`docs/admin-api.md`](docs/admin-api.md) | 開發者 | 後台 API、角色權限、與 Apps Script 後台函式對照 |
| [`docs/ingest.md`](docs/ingest.md) | 開發者 | 文件匯入：各格式解析、切段、段落類型、向量、錯誤訊息 |
| [`docs/langgraph-mapping.md`](docs/langgraph-mapping.md) | 開發者 | 問答流程節點、state、條件邊、搬運順序 |
| [`docs/settings.md`](docs/settings.md) | 所有人 | 全部設定 key、預設值、版本差異、存放位置（自動產生） |

## 共同的設計

- **流程固定、內容可換**：去識別化 → 分流 → 狀態機 → Skill → 檢索 → 組裝 → 生成 → 檢查 → 降級。領域相關的一切（角色稱呼、意圖、回答欄位、檢查項目、提示詞、介面文字、Skill、轉介資源）都是設定，不在程式裡。
- **版本**：基本（問答＋知識庫）／多取向分流（＋Skill、L4、追問）／全功能（＋個案機制、重排、知識圖譜），一鍵套用一組開關，之後可個別調。
- **設定 key 兩版一致**，專案範本 JSON 可以在兩版之間搬。
- **API 金鑰只放後端的秘密儲存區**（Apps Script 指令碼屬性／Python 環境變數），永遠不進前端、程式碼、repo。

## 聊天頁（GitHub Pages）

repo Settings → Pages → Source 選 **GitHub Actions**；`web/chat/config.js` 的 `GAS_URL` 填後端網址；push 後自動部署到 `https://<帳號>.github.io/<repo>/chat/`。只部署 `web/`，repo 裡其他資料夾不會被公開成網頁。
