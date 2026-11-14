"""流程紀錄保留天數（TRACE_RETENTION_DAYS）：超過天數的對話只清掉流程紀錄（trace），對話本身、統計用的欄位都留著。"""
import logging
import os

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("retention")
DEFAULT_DAYS = 90


def retention_days() -> int | None:
    """None＝不清（設成 0）。設錯（不是整數、負數）用預設值並記警告，不讓服務起不來"""
    raw = os.environ.get("TRACE_RETENTION_DAYS", "").strip()
    if not raw:
        return DEFAULT_DAYS
    try:
        n = int(raw)
        if n < 0:
            raise ValueError
    except ValueError:
        log.warning("TRACE_RETENTION_DAYS=%r 不是 0 以上的整數，改用預設 %d 天", raw, DEFAULT_DAYS)
        return DEFAULT_DAYS
    return n or None


def purge_traces(session: Session, days: int, batch: int = 5000) -> int:
    """清掉 days 天前的流程紀錄，回傳清了幾筆。分批提交，不長時間鎖住整張表"""
    total = 0
    while True:
        n = session.execute(text("""
            UPDATE conversations SET trace = NULL WHERE id IN (
              SELECT id FROM conversations WHERE trace IS NOT NULL AND created_at < now() - make_interval(days => :d) LIMIT :b)"""),
            {"d": days, "b": batch}).rowcount
        session.commit()
        total += n
        if n < batch:
            return total


def run_once(factory) -> int:
    days = retention_days()
    if days is None:
        return 0
    with factory() as s:
        n = purge_traces(s, days)
    if n:
        log.info("已清除 %d 筆超過 %d 天的流程紀錄", n, days)
    return n
