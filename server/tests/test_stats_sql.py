"""統計改在資料庫裡算之後，結果要和原本「整批載進 Python 再算」的寫法一模一樣（隨機資料比對）。"""
import random
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.routes.admin_ops import TEST_SESSION, _feedback, _log_query, _pct, log_row, stats_of
from db import models as m
from test_api_admin import env  # noqa: F401


def reference(s, b, days, include_test):
    """改寫前的版本，原封不動留著當對照"""
    all_ = s.scalars(_log_query(b, days, None, None, None, None, include_test).order_by(Conversation.created_at)).all()
    fb = _feedback(s, b.id)
    rows = [log_row(c, fb.get((c.session, c.turn))) for c in all_]
    answered = [r for r in rows if r["type"] in ("answer", "continue", "ai_fallback")]
    ret = [r for r in rows if r["type"] in ("answer", "continue", "ai_fallback", "conservative")]
    ups, downs = sum(1 for r in rows if r["fb"] == "up"), sum(1 for r in rows if r["fb"] == "down")
    st = {
        "turns": len(rows), "sessions": len({r["session"] for r in rows}), "byType": dict(Counter(r["type"] for r in rows)),
        "retrievalTurns": len(ret), "hitRate": _pct(sum(1 for r in ret if r["retrieved"] > 0), len(ret)),
        "citeRate": _pct(sum(r["cited"] for r in answered), sum(r["retrieved"] for r in answered)),
        "avgTop": round(sum(r["top"] for r in ret) / len(ret), 3) if ret else None,
        "rewriteRate": _pct(sum(1 for r in answered if r["rewritten"]), len(answered)),
        "upRate": _pct(ups, ups + downs), "ups": ups, "downs": downs, "copied": sum(1 for r in rows if r["copied"]),
        "avgMs": round(sum(r["ms"] for r in rows) / len(rows)) if rows else None,
    }
    gaps = [{"id": r["id"], "at": r["at"], "message": r["message"], "top": r["top"]} for r in rows
            if (r["type"] in ("conservative", "ai_fallback") or (r["type"] in ("answer", "continue") and r["retrieved"] == 0))][-30:][::-1]
    docs = Counter(src.get("title", "") for c in all_ for src in (c.sources or []) if isinstance(src, dict))
    gap_kinds = Counter((c.quality or {}).get("gap", {}).get("kind") for c in all_ if isinstance(c.quality, dict) and (c.quality.get("gap") or {}).get("kind"))
    l5 = Counter(i.get("type", "其他") for c in all_ if isinstance(c.quality, dict) for a in c.quality.get("l5") or [] for i in a.get("issues") or [])
    ev = Counter(e.get("kind") for c in all_ if isinstance(c.stability, dict) for e in c.stability.get("ev") or [])
    units = sorted({c.unit for c in all_ if c.unit})
    return {"stats": st, "gaps": gaps, "topDocs": [{"title": t, "n": n} for t, n in docs.most_common(10)],
            "quality": {"gapKinds": dict(gap_kinds), "l5": dict(l5)}, "stability": dict(ev), "units": units}


Conversation = m.Conversation


def test_stats_sql_matches_python(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    rnd = random.Random(7)
    now = datetime.now(timezone.utc)
    types = ["answer", "continue", "ai_fallback", "conservative", "followup", "referral", "off"]
    titles = ["手冊", "指引", "Q&A", "", "附錄", "法規", "案例集", "表單", "說明", "流程", "名詞", "其他"]
    n = 0
    for sess in range(25):
        name = (TEST_SESSION if sess % 6 == 0 else "s") + str(sess)
        for turn in range(1, rnd.randint(2, 7)):
            n += 1
            q = rnd.choice([None, "x", [], {"gap": None}, {"gap": {"kind": rnd.choice(["無資料", "低分", ""])},
                                                          "l5": [{"issues": [rnd.choice([{"type": "語氣"}, {"type": "引用"}, {}]) for _ in range(rnd.randint(0, 3))]},
                                                                 {"issues": None}]}])
            stab = rnd.choice([None, {"ev": [{"kind": rnd.choice(["缺欄位", "型別錯誤", "重試"])} for _ in range(rnd.randint(0, 3))] + [{}]}])
            src = rnd.choice([[], "壞掉", [rnd.choice([{"title": rnd.choice(titles)}, {}, 5]) for _ in range(rnd.randint(0, 4))]])
            db.add(m.Conversation(bot_id=bot.id, session=name, turn=turn, unit=rnd.choice(["", "甲", "乙", "丙"]), user_text="問" * rnd.randint(1, 200),
                                  rtype=rnd.choice(types), hits=rnd.randint(0, 6), top_score=rnd.choice([0.0, rnd.random()]), cited=rnd.randint(0, 3),
                                  rewritten=rnd.random() < 0.3, ms=rnd.randint(100, 9000), sources=src, quality=q, stability=stab,
                                  created_at=now - timedelta(days=rnd.uniform(0, 60), seconds=n)))
            for kind in rnd.sample(["有幫助", "沒幫助", "複製回答", "複製", "其他"], rnd.randint(0, 3)):
                db.add(m.Feedback(bot_id=bot.id, session=name, turn=turn, kind=kind))
    db.add(m.Conversation(bot_id=bot2.id, session="other", turn=1, user_text="別的 bot", rtype="answer", hits=1, cited=1, ms=1))
    db.flush()
    assert db.scalar(select(m.Conversation.id).limit(1))
    for days in (None, 30, 7):
        for inc in (False, True):
            got, want = stats_of(db, bot, days, inc), reference(db, bot, days, inc)
            assert got == want, (days, inc)
    assert reference(db, bot, None, True)["stats"]["turns"] == n


def test_stats_sql_tolerates_bad_json(env):  # noqa: F811
    """原本的寫法遇到格式壞掉的 JSON 會整個統計失敗；資料庫版本跳過壞掉的部分"""
    c, db, ws, bot, bot2, users, as_ = env
    db.add(m.Conversation(bot_id=bot.id, session="s", turn=1, user_text="q", rtype="answer", hits=1, cited=1, ms=10, sources="壞掉",
                          quality={"l5": ["壞掉", {"issues": ["x", {"type": "語氣"}]}], "gap": "x"}, stability={"ev": "x"}))
    db.add(m.Conversation(bot_id=bot.id, session="s", turn=2, user_text="q", rtype="answer", hits=1, cited=1, ms=10, sources=[3, {"title": "手冊"}],
                          quality=[1], stability={"ev": [3, {"kind": "重試"}]}))
    db.flush()
    r = stats_of(db, bot, None, False)
    assert r["topDocs"] == [{"title": "手冊", "n": 1}] and r["quality"] == {"gapKinds": {}, "l5": {"語氣": 1}} and r["stability"] == {"重試": 1}
