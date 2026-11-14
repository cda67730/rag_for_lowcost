# 資料模型（Python 版，PostgreSQL 17 + pgvector）

這份是 `server/db/models.py` 與 Alembic 遷移的依據。階層與理由見 `architecture.md`。

## 通則

- 主鍵一律 `uuid`（`gen_random_uuid()`）；給人看、會出現在網址的另有 `slug`。
- 時間一律 `timestamptz`，預設 `now()`。
- 每張表都直接或間接帶 `workspace_id`；Bot 底下的帶 `bot_id`，知識庫底下的帶 `kb_id`。**所有查詢以 `bot_id` 或 `kb_id` 為第一個條件**，並以此建複合索引。
- 狀態欄用 `text` ＋ `CHECK`，不用 PostgreSQL enum（新增狀態不用改型別）。
- 不做軟刪除；要保留歷史的（Skill、設定）有獨立的版本表或稽核紀錄。
- JSON 一律 `jsonb`。
- 下方 SQL 依主題分段，外鍵有前後引用（例如 `memberships` 指向 `bots`）；實際建表順序以 Alembic 遷移為準。

刪除行為：

| 刪除 | 連帶 |
| --- | --- |
| Workspace | 全部 CASCADE |
| Bot | 它的 Flow、設定、Skill、對照表、邀請碼、紀錄、評測 CASCADE；掛載關係 CASCADE（知識庫本身不動） |
| 知識庫 | **被任何 Bot 掛著就拒絕**（RESTRICT），要先卸載；沒掛的話文件、段落、圖譜 CASCADE |
| 文件 | 段落、段落節點標註 CASCADE |
| 使用者 | 成員關係 CASCADE；紀錄裡的 `*_by` 設為 NULL |

---

## 1. 租戶與帳號

```sql
CREATE TABLE workspaces (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug          text NOT NULL UNIQUE,
  name          text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  login         text NOT NULL UNIQUE,              -- 英數字，3–40 字
  display_name  text NOT NULL DEFAULT '',
  pw_hash       text NOT NULL,                     -- argon2id
  token_version int  NOT NULL DEFAULT 0,           -- 加一＝讓所有已發出的 JWT 失效
  failed_count  int  NOT NULL DEFAULT 0,
  locked_until  timestamptz,
  disabled      boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_login_at timestamptz
);

CREATE TABLE memberships (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  bot_id        uuid REFERENCES bots(id) ON DELETE CASCADE,   -- NULL＝整個 Workspace
  role          text NOT NULL CHECK (role IN ('owner','admin','reviewer','analyst')),
  UNIQUE (user_id, workspace_id, bot_id)
);
CREATE INDEX ON memberships (workspace_id, user_id);
```

權限判斷：使用者在某 Bot 的角色 = 該 Bot 的 membership，沒有就取 Workspace 層（`bot_id IS NULL`）的；`owner` 只能掛在 Workspace 層。

```sql
CREATE TABLE audit_log (
  id            bigserial PRIMARY KEY,
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  bot_id        uuid,                               -- 不設 FK，Bot 刪了紀錄還在
  kb_id         uuid,
  user_id       uuid REFERENCES users(id) ON DELETE SET NULL,
  action        text NOT NULL,                      -- settings.update / doc.approve / kb.mount / template.apply / login.fail …
  target        text NOT NULL DEFAULT '',
  detail        jsonb NOT NULL DEFAULT '{}',        -- 變更前後；不放金鑰與對話原文
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON audit_log (workspace_id, at DESC);
```

---

## 2. 機器人與流程

```sql
CREATE TABLE bots (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  slug          text NOT NULL,                      -- 聊天頁 ?bot=<slug>
  name          text NOT NULL,
  disabled      boolean NOT NULL DEFAULT false,     -- 停用後聊天端點回「服務暫停」
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (slug)                                     -- 全站唯一，聊天頁只帶 slug
);

CREATE TABLE flows (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bot_id        uuid NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  kind          text NOT NULL DEFAULT 'chat' CHECK (kind IN ('chat','collect')),
  name          text NOT NULL,                      -- 正式、試用…
  is_default    boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ON flows (bot_id) WHERE is_default;   -- 每個 Bot 恰好一條預設

CREATE TABLE bot_settings (
  bot_id        uuid NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  key           text NOT NULL,
  value         text NOT NULL DEFAULT '',           -- 一律字串，與試算表一致；讀取時依預設值的型別轉換
  updated_by    uuid REFERENCES users(id) ON DELETE SET NULL,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (bot_id, key)
);

CREATE TABLE flow_settings (
  flow_id       uuid NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
  key           text NOT NULL,
  value         text NOT NULL,
  updated_by    uuid REFERENCES users(id) ON DELETE SET NULL,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (flow_id, key)
);
```

設定讀取順序：程式預設（`settings/defaults.py`）→ `bot_settings` → `flow_settings` → 評測的 `settings_override`。只有 `docs/settings.md` 標「Flow 可覆蓋」的 key 會讀 `flow_settings`。

```sql
CREATE TABLE skills (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bot_id        uuid NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  skill_key     text NOT NULL,                      -- S01…，範本與匯出用
  name          text NOT NULL,
  topics        text[] NOT NULL DEFAULT '{}',       -- 對應主題
  always_on     boolean NOT NULL DEFAULT false,
  content       text NOT NULL DEFAULT '',
  enabled       boolean NOT NULL DEFAULT true,
  version       int  NOT NULL DEFAULT 1,
  updated_by    uuid REFERENCES users(id) ON DELETE SET NULL,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (bot_id, skill_key),
  UNIQUE (bot_id, name)
);

CREATE TABLE skill_versions (
  skill_id      uuid NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  version       int  NOT NULL,
  name          text NOT NULL,
  topics        text[] NOT NULL,
  always_on     boolean NOT NULL,
  content       text NOT NULL,
  updated_by    uuid REFERENCES users(id) ON DELETE SET NULL,
  updated_at    timestamptz NOT NULL,
  PRIMARY KEY (skill_id, version)
);

CREATE TABLE synonyms (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bot_id        uuid NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  word          text NOT NULL,                      -- 使用者用語
  concept       text NOT NULL,                      -- 對應概念
  UNIQUE (bot_id, word, concept)
);

CREATE TABLE forbidden_rules (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bot_id        uuid NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  rule          text NOT NULL,                      -- 詞或正規式
  kind          text NOT NULL DEFAULT 'word' CHECK (kind IN ('word','regex')),
  note          text NOT NULL DEFAULT '',
  UNIQUE (bot_id, rule)
);

CREATE TABLE referrals (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bot_id        uuid NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  category      text NOT NULL,                      -- 「系統能力不足」是保守回答的建議對象
  who           text NOT NULL DEFAULT '',
  contact       text NOT NULL DEFAULT '',
  now_action    text NOT NULL DEFAULT '',           -- 現在要做的事
  sort          int  NOT NULL DEFAULT 0,
  UNIQUE (bot_id, category)
);

CREATE TABLE invite_codes (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bot_id        uuid NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  code          text NOT NULL,
  unit          text NOT NULL DEFAULT '',           -- 單位代號，寫進紀錄
  enabled       boolean NOT NULL DEFAULT true,
  trace         boolean NOT NULL DEFAULT false,     -- 觀測：回應附流程紀錄
  expires_at    timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (bot_id, code)
);
```

邀請碼存明碼（管理者要能看到再發給別人），但不寫進對話紀錄；紀錄只存 `invite_code_id` 與 `unit`。

---

## 3. 知識庫

```sql
CREATE TABLE knowledge_bases (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  slug          text NOT NULL,
  name          text NOT NULL,
  description   text NOT NULL DEFAULT '',
  visibility    text NOT NULL DEFAULT 'workspace' CHECK (visibility IN ('workspace','restricted')),
  embed_model   text NOT NULL,                      -- 建庫時固定；換模型＝重建
  embed_dim     smallint NOT NULL,
  chunk_size    int  NOT NULL DEFAULT 500,
  chunk_overlap int  NOT NULL DEFAULT 80,
  auto_kg_tag   boolean NOT NULL DEFAULT false,     -- 上傳時就做圖譜標註（Apps Script 的 enable_node_mapping）
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, slug)
);

CREATE TABLE kb_allowed_bots (                      -- visibility='restricted' 時的白名單
  kb_id         uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  bot_id        uuid NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  PRIMARY KEY (kb_id, bot_id)
);

CREATE TABLE bot_knowledge (                        -- 掛載
  bot_id        uuid NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  kb_id         uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE RESTRICT,
  label         text NOT NULL DEFAULT '',           -- 出處前綴：〔發展〕K1
  min_hits      smallint NOT NULL DEFAULT 0,        -- 檢索時至少保留幾段
  weight        real NOT NULL DEFAULT 1.0,          -- 分數乘數
  enabled       boolean NOT NULL DEFAULT true,
  sort          int  NOT NULL DEFAULT 0,
  PRIMARY KEY (bot_id, kb_id)
);
```

掛載檢查（應用層）：Bot 與知識庫必須同一個 Workspace；`restricted` 的庫必須在 `kb_allowed_bots`；同一個 Bot 掛的庫必須 `embed_model` 相同（查詢向量只算一次）。

```sql
CREATE TABLE documents (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kb_id         uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  title         text NOT NULL,
  source_note   text NOT NULL DEFAULT '',           -- 出處
  evidence      text NOT NULL DEFAULT '',           -- 證據等級
  filename      text NOT NULL DEFAULT '',
  mime          text NOT NULL DEFAULT '',
  size_bytes    int  NOT NULL DEFAULT 0,
  sha256        text NOT NULL DEFAULT '',           -- 重複上傳提醒
  status        text NOT NULL DEFAULT 'processing'
                CHECK (status IN ('processing','pending','approved','disabled','failed')),
  error         text NOT NULL DEFAULT '',
  chunk_count   int  NOT NULL DEFAULT 0,
  uploaded_by   uuid REFERENCES users(id) ON DELETE SET NULL,
  uploaded_at   timestamptz NOT NULL DEFAULT now(),
  reviewed_by   uuid REFERENCES users(id) ON DELETE SET NULL,
  reviewed_at   timestamptz
);
CREATE INDEX ON documents (kb_id, status);

CREATE TABLE document_files (                       -- 原始檔，獨立一張表避免列表查詢載到大欄位
  doc_id        uuid PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  content       bytea NOT NULL                      -- 上限 50 MB（應用層檢查）
);

CREATE TABLE chunks (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kb_id         uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  doc_id        uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  seq           int  NOT NULL,
  text          text NOT NULL,
  page          text NOT NULL DEFAULT '',           -- 頁碼／投影片號／工作表名
  ctype         text NOT NULL DEFAULT '',           -- 具體做法／案例／溝通範例／原則／背景／轉介資訊（配額用，見 ingest.md）
  audience      text[] NOT NULL DEFAULT '{}',
  age_range     text NOT NULL DEFAULT '',
  context       text NOT NULL DEFAULT '',
  active        boolean NOT NULL DEFAULT false,     -- 文件已核可的冗餘，由觸發器維護，程式不要寫
  embed_dim     smallint NOT NULL,                  -- 冗餘一份，給部分索引用
  embedding     vector,                             -- 不定維度，見下方索引
  tsv           tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
  UNIQUE (doc_id, seq)
);
CREATE INDEX ON chunks (kb_id);
CREATE INDEX ON chunks USING gin (tsv);
-- 每種維度一個部分＋運算式索引；新增其他維度的知識庫時，遷移腳本補一條
CREATE INDEX chunks_emb_768 ON chunks USING hnsw ((embedding::vector(768)) vector_cosine_ops)
  WHERE embed_dim = 768 AND active;
CREATE INDEX chunks_emb_1536 ON chunks USING hnsw ((embedding::vector(1536)) vector_cosine_ops)
  WHERE embed_dim = 1536 AND active;      -- 第二個遷移加的
```

**`chunks.active` 觸發器**（定義在初始遷移）：

- `chunks_active_on_insert`（BEFORE INSERT ON chunks）：新段落的 `active` 取自所屬文件是否 `approved`。
- `documents_status_sync`（AFTER UPDATE OF status ON documents）：文件狀態一改，它的段落 `active` 跟著改。

為什麼不 join `documents`：查詢裡一有 join 加過濾，規劃器就不會用 HNSW 索引（驗證時實測確認），而且近似搜尋先取前 k 再過濾，會少回結果。把核可狀態放進段落、放進部分索引的條件，查詢只碰一張表。

**向量查詢**必須帶相同的轉型與條件才會用到索引：

```sql
SELECT id, 1 - (embedding::vector(768) <=> $1::vector(768)) AS score
FROM chunks
WHERE embed_dim = 768 AND active AND kb_id = ANY($2)
ORDER BY embedding::vector(768) <=> $1::vector(768)
LIMIT $3;
```

- 知識庫小（幾千段以內）時規劃器會選 `kb_id` 索引做精確比對，這是對的，結果也比近似搜尋準；知識庫大時才會選 HNSW。
- 一個 Bot 掛多個知識庫時，`kb_id = ANY(...)` 在 HNSW 之後過濾；pgvector 0.8 以上在查詢前設 `SET LOCAL hnsw.iterative_scan = relaxed_order`，索引會繼續往下找直到湊滿 k 筆。檢索服務會在支援時自動設定。每個知識庫各自的「至少取幾段」由檢索服務對該庫另查一次。
- 文件名稱、頁碼等要顯示的欄位，拿到段落 id 後再查 `documents`。

不同維度的知識庫可以並存在同一張表（pgvector 支援不定維度欄位＋部分運算式索引），但同一個 Bot 掛的庫必須同一個向量模型。

**支援的向量模型與維度**（`db/models.py` 的 `SUPPORTED_EMBED_DIMS`；建知識庫時由 `validate_embed_config` 檢查）：

| 模型 | 供應商 | 可用維度 |
| --- | --- | --- |
| `gemini-embedding-001` | Gemini | 768、1536 |
| `text-embedding-3-small` | OpenAI | 768、1536（原生） |
| `text-embedding-3-large` | OpenAI | 768、1536 |
| `text-embedding-ada-002` | OpenAI | 1536（不能指定維度） |

HNSW 索引上限 2000 維，3072 維要改用 `halfvec` 型別，第一版不支援。新增維度＝寫一個遷移加一條 `chunks_emb_<維度>` 索引並更新 `SUPPORTED_EMBED_DIMS`；有測試確認兩者一致。

關鍵字分數：中文沒有斷詞，`tsv` 用 `simple` 設定只對英數有用；中文關鍵字加分沿用 Apps Script 做法（同義詞擴充後的詞在段落裡出現幾次），在應用層算，不靠全文索引。

---

## 4. 知識圖譜（跟著知識庫走）

```sql
CREATE TABLE kg_defs (
  kb_id         uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  kind          text NOT NULL CHECK (kind IN ('type','relation','audience')),
  def_key       text NOT NULL,                      -- hub、hasPart、a1…
  label         text NOT NULL,
  definition    text NOT NULL DEFAULT '',
  PRIMARY KEY (kb_id, kind, def_key)
);

CREATE TABLE kg_sources (
  kb_id         uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  source_key    text NOT NULL,
  title         text NOT NULL,
  publisher     text NOT NULL DEFAULT '',
  url           text NOT NULL DEFAULT '',
  scope         text NOT NULL DEFAULT '',
  PRIMARY KEY (kb_id, source_key)
);

CREATE TABLE kg_nodes (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kb_id         uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  node_key      text NOT NULL,                      -- 匯入檔的節點 ID
  name          text NOT NULL,
  ntype         text NOT NULL,                      -- 對應 kg_defs(kind='type')
  parent_id     uuid REFERENCES kg_nodes(id) ON DELETE SET NULL,
  path          text NOT NULL DEFAULT '',
  definition    text NOT NULL DEFAULT '',
  tips          text NOT NULL DEFAULT '',           -- 實務要點
  source_key    text NOT NULL DEFAULT '',
  aliases       text[] NOT NULL DEFAULT '{}',       -- 同義說法
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  origin        text NOT NULL DEFAULT 'import',     -- import／candidate／generated
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (kb_id, node_key)
);
CREATE INDEX ON kg_nodes (kb_id, status);

CREATE TABLE kg_edges (
  kb_id         uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  subj_id       uuid NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
  rel           text NOT NULL,                      -- 對應 kg_defs(kind='relation')
  obj_id        uuid NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
  note          text NOT NULL DEFAULT '',
  origin        text NOT NULL DEFAULT 'import',
  PRIMARY KEY (subj_id, rel, obj_id)
);
CREATE INDEX ON kg_edges (kb_id, obj_id);

CREATE TABLE kg_links (                             -- 段落 ↔ 節點標註
  chunk_id      uuid NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  node_id       uuid NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
  tag           text NOT NULL DEFAULT 'primary' CHECK (tag IN ('primary','secondary')),
  method        text NOT NULL DEFAULT 'ai',         -- ai／manual
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (chunk_id, node_id)
);
CREATE INDEX ON kg_links (node_id);

CREATE TABLE kg_candidates (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kb_id         uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  name          text NOT NULL,
  definition    text NOT NULL DEFAULT '',
  ntype         text NOT NULL DEFAULT '',
  parent_hint   text NOT NULL DEFAULT '',
  rel_hint      text NOT NULL DEFAULT '',
  doc_id        uuid REFERENCES documents(id) ON DELETE SET NULL,
  chunk_id      uuid REFERENCES chunks(id) ON DELETE SET NULL,
  hits          int  NOT NULL DEFAULT 1,
  reason        text NOT NULL DEFAULT '',
  status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','merged','rejected')),
  result        text NOT NULL DEFAULT '',           -- 合併到哪個節點等
  reviewed_by   uuid REFERENCES users(id) ON DELETE SET NULL,
  reviewed_at   timestamptz
);
CREATE INDEX ON kg_candidates (kb_id, status);
```

---

## 5. 對話紀錄與用量

```sql
CREATE TABLE conversations (
  id            bigserial PRIMARY KEY,
  bot_id        uuid NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  flow_id       uuid REFERENCES flows(id) ON DELETE SET NULL,
  session       text NOT NULL,
  turn          int  NOT NULL,
  invite_code_id uuid REFERENCES invite_codes(id) ON DELETE SET NULL,
  unit          text NOT NULL DEFAULT '',
  user_text     text NOT NULL,                      -- 去識別化後；name_map 不落地
  rtype         text NOT NULL,                      -- answer／continue／ai_fallback／followup／referral／out_of_scope／conservative
  intent        text NOT NULL DEFAULT '',
  triage        jsonb,
  referral      jsonb,
  answer        jsonb,                              -- 依 answer_schema 的欄位
  sources       jsonb NOT NULL DEFAULT '[]',        -- [{ref, chunk_id, doc_id, kb_id, label, title, page}]
  checks        jsonb NOT NULL DEFAULT '[]',
  rewritten     boolean NOT NULL DEFAULT false,
  skills        text[] NOT NULL DEFAULT '{}',
  hits          smallint NOT NULL DEFAULT 0,
  top_score     real NOT NULL DEFAULT 0,
  cited         smallint NOT NULL DEFAULT 0,
  ms            int  NOT NULL DEFAULT 0,
  tokens        jsonb NOT NULL DEFAULT '{}',        -- {input, output} 依模型
  trace         jsonb,                              -- 流程紀錄（與 Apps Script 同格式）
  stability     jsonb,
  quality       jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (bot_id, session, turn)
);
CREATE INDEX ON conversations (bot_id, created_at DESC);
CREATE INDEX ON conversations (bot_id, rtype, created_at DESC);

CREATE TABLE feedback (
  id            bigserial PRIMARY KEY,
  bot_id        uuid NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  session       text NOT NULL,
  turn          int  NOT NULL,
  kind          text NOT NULL,                      -- 有幫助／沒幫助／複製：<欄位>
  note          text NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON feedback (bot_id, created_at DESC);

CREATE TABLE usage_counters (                       -- daily_limit、hourly_limit、邀請碼錯誤次數
  bucket        text NOT NULL,                      -- day:<bot>:<yyyymmdd>／hour:<bot>:<code>:<yyyymmddhh>／badcode:<bot>
  count         int  NOT NULL DEFAULT 0,
  expires_at    timestamptz NOT NULL,
  PRIMARY KEY (bucket)
);
```

用量計數用 `INSERT … ON CONFLICT (bucket) DO UPDATE SET count = usage_counters.count + 1 RETURNING count`，一次往返；過期的列由排程每天清。

保存期限：`conversations.trace` 體積最大，預設 90 天後清成 NULL（可設定），其餘欄位保留。

---

## 6. 評測

```sql
CREATE TABLE eval_questions (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bot_id        uuid NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  kind          text NOT NULL CHECK (kind IN ('answer','retrieval')),   -- retrieval＝原「命中率測試」
  source        text NOT NULL CHECK (source IN ('ai','real','expert')),
  qtype         text NOT NULL DEFAULT '',
  question      text NOT NULL,
  expect_chunks uuid[] NOT NULL DEFAULT '{}',
  expect_docs   uuid[] NOT NULL DEFAULT '{}',
  key_points    text NOT NULL DEFAULT '',
  expect_referral text NOT NULL DEFAULT '',
  expect_rtype  text NOT NULL DEFAULT '',
  status        text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','retired')),
  expert_ok     boolean NOT NULL DEFAULT false,
  bank_version  int  NOT NULL DEFAULT 1,            -- 凍結題庫：跑評測時記錄用的是哪一版
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON eval_questions (bot_id, kind, status);

CREATE TABLE eval_runs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bot_id        uuid NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  flow_id       uuid REFERENCES flows(id) ON DELETE SET NULL,
  kind          text NOT NULL,
  bank_version  int  NOT NULL,
  settings_override jsonb NOT NULL DEFAULT '{}',
  repeat        smallint NOT NULL DEFAULT 1,
  status        text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed','cancelled')),
  summary       jsonb NOT NULL DEFAULT '{}',        -- 五項平均、Hit@K、MRR、門檻判定
  started_by    uuid REFERENCES users(id) ON DELETE SET NULL,
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz
);
CREATE INDEX ON eval_runs (bot_id, started_at DESC);

CREATE TABLE eval_results (
  run_id        uuid NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
  question_id   uuid NOT NULL REFERENCES eval_questions(id) ON DELETE CASCADE,
  attempt       smallint NOT NULL DEFAULT 1,
  rtype         text NOT NULL DEFAULT '',
  found         boolean,                            -- 找對段落
  rank          smallint,
  on_topic      real,                               -- 對題
  coverage      real,                               -- 涵蓋重點
  grounded      real,                               -- 依據
  safe          real,                               -- 安全
  safe_note     text NOT NULL DEFAULT '',
  format_issues text NOT NULL DEFAULT '',
  answer_model  text NOT NULL DEFAULT '',
  judge_model   text NOT NULL DEFAULT '',
  judge_note    text NOT NULL DEFAULT '',
  answer_summary text NOT NULL DEFAULT '',
  ms            int  NOT NULL DEFAULT 0,
  expert_check  text NOT NULL DEFAULT '',
  expert_note   text NOT NULL DEFAULT '',
  PRIMARY KEY (run_id, question_id, attempt)
);
```

---

## 7. 背景任務

```sql
CREATE TABLE jobs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  bot_id        uuid,
  kb_id         uuid,
  kind          text NOT NULL,                      -- ingest／kg_generate／kg_tag／eval_generate／eval_run／repair
  target        text NOT NULL DEFAULT '',           -- doc_id、run_id 等
  status        text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed','cancelled')),
  progress      real NOT NULL DEFAULT 0,
  message       text NOT NULL DEFAULT '',
  result        jsonb,
  created_by    uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz
);
CREATE INDEX ON jobs (workspace_id, created_at DESC);
```

伺服器啟動時把 `running` 的工作標為 `failed`（`message='伺服器重啟'`），前端可重試。

## 8. Apps Script 工作表對照

| 工作表 | Python 表 | 備註 |
| --- | --- | --- |
| 設定 | `bot_settings`（＋`flow_settings`） | `model_embed`、`embed_dim`、`chunk_size`、`chunk_overlap`、`enable_node_mapping` 改成 `knowledge_bases` 的欄位 |
| Skill／Skill版本 | `skills`／`skill_versions` | `對應主題` 由逗號字串改陣列 |
| 文件 | `documents`（＋`document_files`） | 多一層 `kb_id` |
| 知識段落 | `chunks` | 向量由 base64 int8 改 pgvector；`類型` → `ctype`；多 `active`（文件核可的冗餘） |
| 同義詞／禁止用語／轉介資源 | `synonyms`／`forbidden_rules`／`referrals` | |
| 邀請碼 | `invite_codes` | |
| 管理員 | `users`＋`memberships` | 單一層級 → 四種角色 |
| 對話紀錄／回饋 | `conversations`／`feedback` | 回答、出處、檢查、流程紀錄改 jsonb |
| 節點／關係／圖譜來源／段落節點／新節點候選／圖譜定義 | `kg_nodes`／`kg_edges`／`kg_sources`／`kg_links`／`kg_candidates`／`kg_defs` | 跟著知識庫，不跟著 Bot |
| 評測題庫／評測結果／檢索測試 | `eval_questions`／`eval_runs`＋`eval_results` | 檢索測試併入 `kind='retrieval'` |
| （快取中的計數） | `usage_counters` | Apps Script 用 CacheService |

搬資料時：一份試算表 → 一個 Bot ＋ 一個同名知識庫（自動掛載）；向量需要用新的知識庫設定重算，不直接搬。
