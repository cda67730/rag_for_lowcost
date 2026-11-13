# Python 版後端（開發中）

LangGraph + FastAPI + PostgreSQL/pgvector，多機器人。

先讀這三份，順序如下：
1. [`../docs/architecture.md`](../docs/architecture.md) — 階層（Workspace → Bot → Flow、知識庫獨立）、元件、路由、權限、資料表、目錄、待確認決定
2. [`../docs/api.md`](../docs/api.md) — 聊天頁依賴的介面，與 Apps Script 版一致
3. [`../docs/langgraph-mapping.md`](../docs/langgraph-mapping.md) — 每個節點對應 Apps Script 的哪一段、state、搬運順序

程式尚未開始；架構確認後依 architecture.md 第 12 節的順序實作。

金鑰只放 `.env`（已在 `.gitignore`），不進程式碼、不進資料庫。
