# Python 版後端（開發中）

LangGraph + FastAPI + PostgreSQL/pgvector。對外介面見 [`../docs/api.md`](../docs/api.md)，與 Apps Script 版的對照見 [`../docs/langgraph-mapping.md`](../docs/langgraph-mapping.md)。

預計結構：

```
server/
├── app/          FastAPI 入口、路由（/api/ui /api/check /api/chat /api/feedback /api/ingest /api/eval）
├── graph/        LangGraph 節點與圖（state 定義見 docs/langgraph-mapping.md）
├── db/           SQLAlchemy 模型、Alembic 遷移
├── services/     llm（Gemini／OpenAI）、embedding、ingest（文件解析切段）、kg
├── settings/     專案設定載入與 {user}{subject}{escalate}{domain} 替換
├── tests/
├── pyproject.toml
├── .env.example  資料庫連線、模型金鑰（GEMINI_API_KEY、OPENAI_API_KEY）、CORS
└── docker-compose.yml  Postgres 17 + pgvector + app
```

金鑰只放 `.env`（已在 `.gitignore`），不進程式碼。
