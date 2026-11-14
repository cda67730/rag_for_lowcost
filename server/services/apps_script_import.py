"""從 Apps Script 版搬過來：讀試算表下載的 .xlsx（只讀不寫），建一個 Bot＋一個同名知識庫。

搬：設定、Skill（含版本紀錄）、同義詞、禁止用語、轉介資源、邀請碼、文件與段落、知識圖譜（定義、節點、關係、來源、段落標註、候選）、評測題庫；
選搬：對話紀錄與回饋（with_logs）。
不搬：管理員帳號（密碼格式不同，後台成員重新建）、評測結果、舊向量（依新知識庫的設定重算）。
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (SUPPORTED_EMBED_DIMS, Bot, BotKnowledge, Chunk, Conversation, Document, EvalQuestion, Feedback, Flow, ForbiddenRule, InviteCode,
                       KgCandidate, KgDef, KgEdge, KgLink, KgNode, KgSource, KnowledgeBase, Referral, Skill, SkillVersion, Synonym, Workspace)
from services.evaluation import EVAL_EXPECT
from services.ingest import embed_all, embed_text
from services.llm import EMBED_MODELS
from settings.loader import KB_LEVEL, SETTING_DEFAULTS, TEXT_DEFAULTS, js_str
from settings.store import SettingsError, save_bot_settings

# 與 apps-script/Store.gs 的 SHEETS、HEADER_ALIAS 相同
SHEETS = {"settings": "設定", "skills": "Skill", "skillHist": "Skill版本", "docs": "文件", "chunks": "知識段落", "synonyms": "同義詞", "forbidden": "禁止用語",
          "referral": "轉介資源", "codes": "邀請碼", "logs": "對話紀錄", "feedback": "回饋", "kgNodes": "節點", "kgEdges": "關係", "kgSources": "圖譜來源",
          "kgLinks": "段落節點", "kgCands": "新節點候選", "kgDefs": "圖譜定義", "evalQs": "評測題庫"}
HEADER_ALIAS = {"園所": "單位", "老師訊息（去識別化）": "使用者訊息", "老師用語": "使用者用語", "園所代號": "單位代號"}
DOC_STATUS = {"已核可": "approved", "待審核": "pending", "停用": "disabled"}
DEF_KIND = {"節點類型": "type", "關係": "relation", "讀者對象": "audience"}
EVAL_SOURCE = {"AI 出題": "ai", "真實提問": "real", "專家": "expert"}
CAND_STATUS = {"待審核": "pending", "已核可": "accepted", "已合併": "merged", "略過": "rejected"}


class ImportError_(ValueError):
    """訊息給使用者看"""


def read_workbook(data: bytes) -> dict[str, list[dict]]:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise ImportError_("不是有效的 .xlsx：" + type(e).__name__) from None
    out: dict[str, list[dict]] = {}
    names = {v: k for k, v in SHEETS.items()}
    try:
        for ws in wb.worksheets:
            key = names.get(ws.title)
            if not key:
                continue
            rows = ws.iter_rows(values_only=True)
            head = next(rows, None)
            if not head:
                out[key] = []
                continue
            head = [HEADER_ALIAS.get(str(h or "").strip(), str(h or "").strip()) for h in head]
            out[key] = [{h: v for h, v in zip(head, r) if h} for r in rows if any(c not in (None, "") for c in r)]
    finally:
        wb.close()
    if "settings" not in out or "docs" not in out:
        raise ImportError_("找不到「設定」或「文件」工作表，請確認是 Apps Script 版的試算表")
    return out


def s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v).strip()


def truthy(v) -> bool:
    return v is True or s(v).upper() == "TRUE" or s(v) == "是"


def as_time(v) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        t = datetime.fromisoformat(s(v))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def as_json(v, default=None):
    if v in (None, ""):
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(str(v))
    except ValueError:
        return default


def as_int(v, default=0) -> int:
    try:
        return int(float(s(v))) if s(v) else default
    except ValueError:
        return default


def as_float(v, default=0.0) -> float:
    try:
        return float(s(v)) if s(v) else default
    except ValueError:
        return default


async def import_workbook(session: Session, llm, data: bytes, *, workspace_slug: str, bot_slug: str, kb_slug: str | None = None,
                          with_logs: bool = False, embed: bool = True, progress=print) -> dict:
    wb = read_workbook(data)
    ws = session.scalar(select(Workspace).where(Workspace.slug == workspace_slug))
    if ws is None:
        raise ImportError_(f"找不到工作區「{workspace_slug}」")
    if session.scalar(select(Bot.id).where(Bot.slug == bot_slug)):
        raise ImportError_(f"機器人 slug「{bot_slug}」已被使用")
    kb_slug = kb_slug or bot_slug
    if session.scalar(select(KnowledgeBase.id).where(KnowledgeBase.workspace_id == ws.id, KnowledgeBase.slug == kb_slug)):
        raise ImportError_(f"知識庫 slug「{kb_slug}」已被使用")
    rep: dict[str, Any] = {"skipped": []}
    settings = {s(r.get("設定")): r.get("值") for r in wb["settings"] if s(r.get("設定"))}

    # Bot、預設流程、知識庫（向量模型沿用舊設定，不支援就用預設）
    bot = Bot(workspace_id=ws.id, slug=bot_slug, name=s(settings.get("bot_name")) or bot_slug)
    session.add(bot); session.flush()
    session.add(Flow(bot_id=bot.id, kind="chat", name="預設", is_default=True))
    model, dim = s(settings.get("model_embed")) or "gemini-embedding-001", as_int(settings.get("embed_dim"), 768)
    if model not in EMBED_MODELS or dim not in SUPPORTED_EMBED_DIMS or dim not in EMBED_MODELS[model]["dims"]:
        rep["skipped"].append(f"向量模型 {model}・{dim} 不支援，改用 gemini-embedding-001・768")
        model, dim = "gemini-embedding-001", 768
    kb = KnowledgeBase(workspace_id=ws.id, slug=kb_slug, name=bot.name, embed_model=model, embed_dim=dim,
                       chunk_size=as_int(settings.get("chunk_size"), 500) or 500, chunk_overlap=as_int(settings.get("chunk_overlap"), 80),
                       auto_kg_tag=truthy(settings.get("enable_node_mapping")))
    session.add(kb); session.flush()
    session.add(BotKnowledge(bot_id=bot.id, kb_id=kb.id, label=kb.name))

    # 設定：不合法的略過並記下
    vals = {k: v for k, v in settings.items() if (k in SETTING_DEFAULTS or k in TEXT_DEFAULTS) and k not in KB_LEVEL}
    unknown = [k for k in settings if k not in SETTING_DEFAULTS and k not in TEXT_DEFAULTS]
    if unknown:
        rep["skipped"].append("不認得的設定：" + "、".join(unknown[:20]))
    for _ in range(3):
        try:
            rep["settings"] = len(save_bot_settings(session, bot.id, vals))
            break
        except SettingsError as e:
            for k, m in e.fields.items():
                rep["skipped"].append(f"設定 {k}：{m}")
                vals.pop(k, None)

    # Skill 與版本紀錄
    hist: dict[str, list[dict]] = {}
    for r in wb.get("skillHist", []):
        hist.setdefault(s(r.get("Skill ID")), []).append(r)
    rep["skills"] = 0
    for r in wb.get("skills", []):
        if not s(r.get("名稱")):
            continue
        key = s(r.get("Skill ID")) or f"S{rep['skills'] + 1:02d}"
        k = Skill(bot_id=bot.id, skill_key=key, name=s(r.get("名稱")), topics=[t.strip() for t in s(r.get("對應主題")).replace("，", ",").replace("、", ",").split(",") if t.strip()],
                  always_on=truthy(r.get("一律載入")), content=s(r.get("內容")), enabled=s(r.get("啟用")) == "" or truthy(r.get("啟用")),
                  version=max(1, as_int(r.get("版本"), 1)), updated_at=as_time(r.get("更新時間")) or datetime.now(timezone.utc))
        session.add(k); session.flush()
        versions = {as_int(h.get("版本"), 1): h for h in hist.get(key, [])}
        versions.setdefault(k.version, {"名稱": k.name, "對應主題": "、".join(k.topics), "一律載入": k.always_on, "內容": k.content})
        for v, h in sorted(versions.items()):
            session.add(SkillVersion(skill_id=k.id, version=v, name=s(h.get("名稱")) or k.name, topics=[t.strip() for t in s(h.get("對應主題")).replace("，", "、").replace(",", "、").split("、") if t.strip()],
                                     always_on=truthy(h.get("一律載入")), content=s(h.get("內容")), updated_at=as_time(h.get("更新時間")) or datetime.now(timezone.utc)))
        rep["skills"] += 1

    # 對照表與邀請碼（重複的只留第一筆）
    seen: set = set()
    for r in wb.get("synonyms", []):
        w, c = s(r.get("使用者用語")), s(r.get("對應概念"))
        if w and c and (w, c) not in seen:
            seen.add((w, c))
            session.add(Synonym(bot_id=bot.id, word=w, concept=c))
    rep["synonyms"] = len(seen)
    seen = set()
    for r in wb.get("forbidden", []):
        rule = s(r.get("詞或規則"))
        if rule and rule not in seen:
            seen.add(rule)
            session.add(ForbiddenRule(bot_id=bot.id, rule=rule, kind="regex" if s(r.get("類型")) in ("正規式", "regex") else "word", note=s(r.get("說明"))))
    rep["forbidden"] = len(seen)
    seen = set()
    for i, r in enumerate(wb.get("referral", [])):
        c = s(r.get("類別"))
        if c and c not in seen:
            seen.add(c)
            session.add(Referral(bot_id=bot.id, category=c, who=s(r.get("該找誰")), contact=s(r.get("聯絡方式")), now_action=s(r.get("現在要做的事")), sort=i))
    rep["referral"] = len(seen)
    seen = set()
    for r in wb.get("codes", []):
        c = s(r.get("邀請碼")).upper()
        if c and c not in seen:
            seen.add(c)
            session.add(InviteCode(bot_id=bot.id, code=c, unit=s(r.get("單位代號")), enabled=truthy(r.get("啟用")), trace=truthy(r.get("觀測"))))
    rep["invite_codes"] = len(seen)
    session.flush()

    # 文件與段落（沒有原始檔：存段落文字合起來的內容，之後不能「重新處理」，但可以審核、分類、標註）
    doc_map: dict[str, uuid.UUID] = {}
    chunks_by_doc: dict[str, list[dict]] = {}
    for r in wb.get("chunks", []):
        chunks_by_doc.setdefault(s(r.get("文件ID")), []).append(r)
    chunk_map: dict[str, uuid.UUID] = {}
    new_chunks: list[tuple[Chunk, str]] = []
    olds: list[str] = []
    for r in wb["docs"]:
        old = s(r.get("文件ID"))
        if not old:
            continue
        rows = sorted(chunks_by_doc.get(old, []), key=lambda x: as_int(x.get("序號")))
        text = "\n\n".join(s(c.get("內容")) for c in rows)
        d = Document(kb_id=kb.id, title=s(r.get("標題")) or old, source_note=s(r.get("出處")), evidence=s(r.get("證據等級")), filename=s(r.get("原始檔名")),
                     mime="", size_bytes=len(text.encode()), sha256=hashlib.sha256(("apps-script:" + old + text).encode()).hexdigest(),
                     status=DOC_STATUS.get(s(r.get("狀態")), "pending"), chunk_count=len(rows), uploaded_at=as_time(r.get("上傳時間")) or datetime.now(timezone.utc),
                     reviewed_at=as_time(r.get("審核時間")), error="從 Apps Script 版搬來（沒有原始檔）")
        session.add(d); session.flush()
        doc_map[old] = d.id
        for i, c in enumerate(rows):
            if not s(c.get("內容")):
                continue
            ch = Chunk(kb_id=kb.id, doc_id=d.id, seq=as_int(c.get("序號"), i + 1) or i + 1, text=s(c.get("內容")), page=s(c.get("頁碼")), ctype=s(c.get("類型")),
                       audience=[a for a in s(c.get("讀者對象")).split("、") if a], age_range=s(c.get("適用年齡")), context=s(c.get("情境")), embed_dim=kb.embed_dim)
            session.add(ch)
            new_chunks.append((ch, d.title))
            olds.append(s(c.get("段落ID")))
    session.flush()
    for (ch, _), old in zip(new_chunks, olds):
        chunk_map[old] = ch.id
    orphans = sum(len(v) for k, v in chunks_by_doc.items() if k not in doc_map)
    if orphans:
        rep["skipped"].append(f"{orphans} 個段落找不到所屬文件")
    rep["documents"], rep["chunks"] = len(doc_map), len(new_chunks)
    progress(f"文件 {len(doc_map)}、段落 {len(new_chunks)}")

    # 知識圖譜
    rep["kg"] = _import_kg(session, kb.id, wb, chunk_map, doc_map, rep)

    # 評測題庫
    n = 0
    for r in wb.get("evalQs", []):
        q = s(r.get("題目"))
        if not q:
            continue
        t = s(r.get("類型")) or "一般"
        session.add(EvalQuestion(bot_id=bot.id, kind="answer", source=EVAL_SOURCE.get(s(r.get("來源")), "expert"), qtype=t, question=q[:500],
                                 expect_chunks=[chunk_map[x] for x in s(r.get("應命中段落ID")).split(";") if x in chunk_map],
                                 expect_docs=[doc_map[x] for x in s(r.get("應命中文件ID")).split(";") if x in doc_map], key_points=s(r.get("應包含重點")),
                                 expect_referral=s(r.get("應轉介")), expect_rtype=s(r.get("期望回應")) or EVAL_EXPECT.get(t, "answer"),
                                 status="retired" if s(r.get("狀態")) == "停用" else "active", expert_ok=s(r.get("專家確認")) == "是"))
        n += 1
    rep["eval_questions"] = n

    # 對話紀錄與回饋（選搬）
    rep["logs"] = rep["feedback"] = 0
    if with_logs:
        for r in wb.get("logs", []):
            ans = as_json(r.get("回答"))
            session.add(Conversation(bot_id=bot.id, session=s(r.get("Session")), turn=as_int(r.get("輪次"), 1), unit=s(r.get("單位")), user_text=s(r.get("使用者訊息")),
                                     rtype=s(r.get("回應類型")) or "answer", triage=as_json(r.get("分流")), referral={"category": s(r.get("轉介"))} if s(r.get("轉介")) else None,
                                     answer=ans if isinstance(ans, dict) else ({"text": s(r.get("回答"))} if s(r.get("回答")) else None),
                                     sources=as_json(r.get("引用"), []) or [], checks=as_json(r.get("檢查"), []) or [], rewritten=s(r.get("是否重寫")) == "是",
                                     skills=[x for x in s(r.get("載入Skill")).replace("，", "、").split("、") if x], hits=as_int(r.get("檢索段數")),
                                     top_score=as_float(r.get("最高相似度")), cited=as_int(r.get("被引用段數")), ms=as_int(r.get("耗時ms")),
                                     trace=as_json(r.get("流程紀錄"), []) or [], stability=as_json(r.get("穩定度")), quality=as_json(r.get("品質問題")),
                                     created_at=as_time(r.get("時間")) or datetime.now(timezone.utc)))
            rep["logs"] += 1
        for r in wb.get("feedback", []):
            session.add(Feedback(bot_id=bot.id, session=s(r.get("Session")), turn=as_int(r.get("輪次")), kind=s(r.get("類型")), note=s(r.get("備註"))[:500],
                                 created_at=as_time(r.get("時間")) or datetime.now(timezone.utc)))
            rep["feedback"] += 1
    session.flush()

    # 向量依新知識庫重算
    rep["missing_vectors"] = len(new_chunks)
    if embed and new_chunks:
        progress(f"計算向量（{len(new_chunks)} 段）…")
        vecs = await embed_all(llm, kb.embed_model, kb.embed_dim, [embed_text(t, c.page, c.text) for c, t in new_chunks])
        for (c, _), v in zip(new_chunks, vecs):
            c.embedding = v
        rep["missing_vectors"] = sum(1 for v in vecs if v is None)
    session.flush()
    rep.update(bot_id=str(bot.id), kb_id=str(kb.id))
    return rep


def _import_kg(session: Session, kb_id, wb: dict, chunk_map: dict, doc_map: dict, rep: dict) -> dict:
    out = {"defs": 0, "nodes": 0, "edges": 0, "sources": 0, "links": 0, "candidates": 0}
    seen: set = set()
    for r in wb.get("kgDefs", []):
        kind, key = DEF_KIND.get(s(r.get("種類"))), s(r.get("ID"))
        if kind and key and s(r.get("名稱")) and (kind, key) not in seen:
            seen.add((kind, key))
            session.add(KgDef(kb_id=kb_id, kind=kind, def_key=key, label=s(r.get("名稱")), definition=s(r.get("定義"))))
            out["defs"] += 1
    nodes: dict[str, KgNode] = {}
    for r in wb.get("kgNodes", []):
        key = s(r.get("節點ID"))
        if not key or key in nodes or not s(r.get("名稱")):
            continue
        n = KgNode(kb_id=kb_id, node_key=key, name=s(r.get("名稱")), ntype=s(r.get("類型")), path=s(r.get("路徑")), definition=s(r.get("定義")), tips=s(r.get("實務要點")),
                   source_key=s(r.get("來源ID")), aliases=[a.strip() for a in s(r.get("同義說法")).replace(",", "、").replace("，", "、").split("、") if a.strip()],
                   status="disabled" if s(r.get("狀態")) == "停用" else "active", origin="import" if s(r.get("來源")).startswith("知識圖譜匯入") else "candidate")
        session.add(n)
        nodes[key] = n
    session.flush()
    for r in wb.get("kgNodes", []):
        n, p = nodes.get(s(r.get("節點ID"))), s(r.get("上層"))
        if n is not None and p in nodes:
            n.parent_id = nodes[p].id
    out["nodes"] = len(nodes)
    seen = set()
    for r in wb.get("kgEdges", []):
        a, b, rel = s(r.get("主詞")), s(r.get("受詞")), s(r.get("關係")) or "relatedTo"
        if a in nodes and b in nodes and (a, rel, b) not in seen:
            seen.add((a, rel, b))
            session.add(KgEdge(kb_id=kb_id, subj_id=nodes[a].id, rel=rel, obj_id=nodes[b].id, note=s(r.get("關係說明")),
                               origin="import" if s(r.get("來源")).startswith("知識圖譜匯入") else "candidate"))
    out["edges"] = len(seen)
    seen = set()
    for r in wb.get("kgSources", []):
        k = s(r.get("來源ID"))
        if k and k not in seen:
            seen.add(k)
            session.add(KgSource(kb_id=kb_id, source_key=k, title=s(r.get("標題")), publisher=s(r.get("出版單位")), url=s(r.get("網址")), scope=s(r.get("範圍"))))
    out["sources"] = len(seen)
    best: dict[tuple, str] = {}
    for r in wb.get("kgLinks", []):
        c, n = chunk_map.get(s(r.get("段落ID"))), nodes.get(s(r.get("節點ID")))
        if c and n:
            tag = "secondary" if s(r.get("標籤")) == "次要" else "primary"
            if (c, n.id) not in best or tag == "primary":
                best[(c, n.id)] = tag
    for (c, nid), tag in best.items():
        session.add(KgLink(chunk_id=c, node_id=nid, tag=tag, method="import"))
    out["links"] = len(best)
    for r in wb.get("kgCands", []):
        if not s(r.get("名稱")):
            continue
        ids = [x for x in s(r.get("段落ID")).split(";") if x]
        session.add(KgCandidate(kb_id=kb_id, name=s(r.get("名稱"))[:40], definition=s(r.get("定義")), ntype=s(r.get("類型")) or "概念", parent_hint=s(r.get("建議上層")),
                                rel_hint=s(r.get("建議關係")), doc_id=doc_map.get(s(r.get("文件ID"))), chunk_id=chunk_map.get(ids[0]) if ids else None,
                                hits=max(1, as_int(r.get("次數"), 1)), reason=s(r.get("理由")), status=CAND_STATUS.get(s(r.get("狀態")), "pending"), result=s(r.get("處理結果"))))
        out["candidates"] += 1
    session.flush()
    return out
