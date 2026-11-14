# Apps Script → LangGraph 節點對照

`apps-script/Chat.gs` 的 `runChat_()` 是一個 400 行的直線程序，中間用 `return finish(...)` 提早結束。搬到 LangGraph 時把每一段變成節點，提早結束變成條件邊指向 `finish`。這份文件是搬運時的對照表。

## State

```python
class ChatState(TypedDict, total=False):
    # 輸入（來自 API 請求）
    bot_id: str
    flow_id: str
    kb_ids: list[str]            # 這個 Bot 掛的知識庫（含標籤與配額，放在 cfg）
    session: str
    message: str                 # 原文
    history: list[dict]          # [{role, text}]
    name_map: dict[str, str]
    followups: int
    answered: int
    feedback: str | None
    trace_on: bool

    # 設定快照（進入圖時讀一次，整張圖用同一份）
    cfg: dict
    roles: dict                  # {domain, user, subject, escalate}
    schema: list[dict]           # answer_schema 解析後
    mode: Literal["full", "continue"]

    # 去識別化
    deid_text: str
    deid_pending: bool           # ai 模式且併入 L4 時為 True

    # L4 / 檢索
    triage: dict                 # intent, intent_key, topics, focus, in_scope, info_enough, missing, referral
    intent_key: str
    is_new: bool
    case_text: str               # prevTeacher + deid_text
    query_vecs: list[list[float]]
    expanded: dict               # 同義詞擴充結果 {text, concepts}
    kg: dict | None              # kgMatch_ 結果
    search: dict                 # searchDetail_ 結果 {cands, hits, minScore, topK}
    hits: list[dict]             # 最後採用的段落
    fallback: bool               # 知識不足、由 AI 補足

    # Skill / prompt
    skills: list[dict]
    skill_text: str
    prompt: str

    # 生成與檢查
    raw_answer: dict
    answer: dict
    checks: dict                 # {pass, items, reasons, relFail}
    attempt: int                 # 0 第一次、1 重寫
    rewritten: bool

    # 輸出
    result: dict                 # 對應 API 的 payload（type 與其餘欄位）
    trace: list[dict]
    metrics: dict
    stability: dict
```

設定快照：`cfg` 在 `load_config` 節點讀一次，之後所有節點只讀 state，不再查資料庫；評測時的 `settings_override` 就是在這裡合併進去。

## 節點

| 節點 | 對應 Chat.gs | 做什麼 | 下一步 |
| --- | --- | --- | --- |
| `load_config` | `cfgAll_()`、`roles_()`、`schema_()`、`intents_()` | 讀 Bot＋Flow 設定、角色、回答欄位、掛的知識庫；決定 `mode` 的前置資料 | `deid` |
| `deid` | 第 1 段 `useDeid` / `deid_()` | `enable_deid` 關→原文；`rule`→規則；`ai` 且 L4 開→只套用已知 `name_map`、標 `deid_pending`；`ai` 且 L4 關→單獨呼叫模型 | `expand_query` |
| `expand_query` | `expandQuery_()` | 同義詞擴充、組 `case_text` | `triage_and_embed` |
| `triage_and_embed` | 第 2 段 `fetchAll([triReq, embReq])` | **並行**：L4 分流（含 `deid_pending` 時的名字清單）＋查詢向量。L4 關→關鍵字選主題、意圖依 history 推 | `apply_deid_result` |
| `apply_deid_result` | `if (mergeDeid)` 區塊 | L4 回的 `deid` 清單套成代稱，`focus`／`missing`／`reason` 一併換；重算 `case_text` | `route_state` |
| `route_state` | 第 3 段 P04 | 條件邊：緊急→`finish_referral`；`off`／`in_scope=false`→`finish_out_of_scope`；資訊不足且可追問→`followup`；否則→`load_skills` | 見右 |
| `followup` | `followupPrompt_` 呼叫 | 一次模型呼叫，產生同理句＋最多 2 題 | `finish` |
| `load_skills` | `pick` / `skillText` | 依主題挑 Skill，沒有就用 `default_skill`；決定 `mode`（`enable_case_flow`、`full_answer_rounds`、`specific`） | `kg_match` |
| `kg_match` | `kgMatch_()` | `enable_kg` 開才做；純比對不呼叫模型 | `retrieve` |
| `retrieve` | `searchDetail_()` | 對 Bot 掛的每個知識庫查向量＋關鍵字（＋圖譜加分／only），依 `bot_knowledge` 的配額合併；`enable_rerank` 開→AI 重排與配額 | `judge_knowledge` |
| `judge_knowledge` | 第 5 段「知識足夠判斷」 | 沒有可用段落或只有背景原則：`knowledge_only` 開→`finish_conservative`；關→`fallback=True` 繼續 | `assemble` |
| `assemble` | `assemble_()` / `assembleContinue_()` | 五層 prompt；依 `mode` 與 `schema` 產生輸出格式 | `generate` |
| `generate` | `genJson_(C.model_answer, prompt)` | 一次模型呼叫；`validateAnswer_`、`cleanAnswer_`；回報 `knowledge_gap` 時依規則轉 `finish_conservative` 或重寫 | `check` |
| `check` | `checks_()` | P08 禁止用語（程式）、P09 出處核對（程式，`enable_citation`）、L5 AI 檢查＋回應度（一次模型呼叫，可關） | `route_check` |
| `route_check` | 重寫迴圈 | 通過→`finish_answer`；未通過且 `attempt=0`→`attempt=1` 回 `assemble`（帶 feedback）；仍未通過→`finish_conservative` | 見右 |
| `finish_*` | `finish({...})` | 組 API payload、寫對話紀錄（評測時不寫）、附 trace | END |

### 圖

```
load_config → deid → expand_query → triage_and_embed → apply_deid_result → route_state
   route_state ─緊急─────────────▶ finish_referral
   route_state ─超出範圍────────▶ finish_out_of_scope
   route_state ─資訊不足────────▶ followup ─▶ finish
   route_state ─其他────────────▶ load_skills → kg_match → retrieve → judge_knowledge
judge_knowledge ─沒知識且 knowledge_only─▶ finish_conservative
judge_knowledge ─其他─────────────────▶ assemble → generate → check → route_check
   route_check ─通過───────────▶ finish_answer
   route_check ─重寫一次───────▶ assemble（attempt=1）
   route_check ─仍不過─────────▶ finish_conservative
```

## 模型呼叫數（與 Apps Script 相同）

| 情況 | 呼叫 |
| --- | --- |
| 基本版一題 | 向量 1 ＋ 生成 1 ＋ L5 1（可關）＝ 2–3 |
| 多取向分流 | ＋ L4 1（去識別化併在裡面） |
| 全功能 | ＋ 重排 1 |
| 重寫 | ＋ 生成 1 ＋ L5 1 |

LangGraph 原生支援的 `triage_and_embed` 並行用 `Send` 或在節點內 `asyncio.gather` 即可。

## 哪些東西直接搬、哪些重寫

| 直接搬（邏輯一樣，改語法） | 重寫 |
| --- | --- |
| 所有 prompt 組裝字串（`triagePrompt_`、`assemble_`、`assembleContinue_`、`checks_` 的 prompt） | `Store.gs` 全部 → SQLAlchemy 模型與查詢 |
| `schema_`／`parseSchema_`／`schemaFormat_`／`cleanAnswer_`／`validateAnswer_` | `Llm.gs` → 供應商 SDK（Gemini、OpenAI），保留「依模型 ID 自動判斷供應商、不相容參數自動重送」 |
| `deidRule_`／`deidScrub_`／`deidApply_`／`deidFromItems_` | `Knowledge.gs` 的向量比對 → pgvector 查詢（`<=>` 餘弦距離）；關鍵字加分用 `tsvector` 或簡單 `ILIKE` |
| `kgMatch_`／`kgPromptText_` | 文件解析：PDF 用 `pypdf`／`pdfplumber`，Word 用 `python-docx`，掃描檔再考慮 OCR；不再用模型讀 PDF |
| `intents_`／`checkItems_`／`roles_`／`tpl_` | 後台：FastAPI 提供 JSON API，前端沿用 `AdminUI.html` 的結構重寫成獨立 SPA（或先用 Jinja 模板） |
| `TIERS`、`DESCRIBE_KINDS`、`describeNormalize_` | 登入與權限：真正的使用者表＋角色（管理者／審核者／研究者），取代單一層級 |
| 評測邏輯 `Eval.gs` 的出題與評分 prompt | 排程：Apps Script 觸發器 → APScheduler 或 Celery |

## 資料表（最小集合；多機器人版的完整表見 architecture.md）

```
bots                id, workspace_id, slug, name
flows               id, bot_id, kind, name, is_default
bot_settings        bot_id, key, value            ← 與設定表一對一
flow_settings       flow_id, key, value           ← 覆蓋
knowledge_bases     id, workspace_id, name
bot_knowledge       bot_id, kb_id, label, min_hits, weight
documents           kb_id, doc_id, title, source, status(pending|approved|disabled)
chunks              kb_id, chunk_id, doc_id, page, text, type, embedding vector(n)
skills / skill_versions / synonyms / forbidden_rules / referrals / invite_codes   bot_id
kg_nodes / kg_edges / kg_links / kg_defs / kg_candidates                   kb_id
conversations       bot_id, flow_id, session, turn, unit, deid_text, type, intent, answer jsonb, sources jsonb, checks jsonb, metrics jsonb, trace jsonb
feedback            bot_id, session, turn, kind, note
eval_questions / eval_runs / eval_results                                  bot_id, flow_id
jobs / audit_log / usage_counters                                          （見 data-model.md）
users / memberships / workspaces
```

`chunks.embedding` 用 pgvector，建 HNSW 索引；維度跟著 `embed_dim` 設定走，換模型要重建欄位。

## 搬運順序建議

1. `db/` 模型＋Alembic＋`docker-compose.yml`（Postgres 17 + pgvector）
2. `settings/`：讀 `bot_settings`＋`flow_settings`、合併預設、`tpl_` 替換——這一步做完，範本 JSON 就能匯入
3. `services/llm.py`：Gemini／OpenAI 兩家、JSON 模式、不相容參數重送
4. `graph/`：照上表一個節點一個節點搬，每個節點寫單元測試（用假的 LLM 回應）
5. `app/`：`/api/ui`、`/api/check`、`/api/chat`、`/api/feedback`——到這裡 `web/chat/` 可以直接接
6. `services/ingest.py`＋`POST /api/admin/kbs/{kb}/documents`（規格見 ingest.md）
7. 後台 API 與介面
8. 評測與 Langfuse 接線
