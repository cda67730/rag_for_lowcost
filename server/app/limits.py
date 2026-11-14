"""用量計數與邀請碼錯誤次數（usage_counters 表）。對應 apps-script/Store.gs 的 dailyOk_、rateOk_、verifyCode_。

Apps Script 拿不到來源 IP，錯誤次數只能全域計；這裡依「機器人＋來源 IP」計，一個人猜錯不會鎖住所有人。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

CODE_FAIL_MAX, CODE_FAIL_WINDOW, CODE_LOCK_SECONDS = 20, 600, 900


def tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Taipei"))


def bump(session: Session, bucket: str, ttl: int) -> int:
    """計數加一並回傳新值；過期的列從 1 重新算"""
    exp = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    return session.execute(text(
        "INSERT INTO usage_counters (bucket, count, expires_at) VALUES (:b, 1, :e) "
        "ON CONFLICT (bucket) DO UPDATE SET "
        "count = CASE WHEN usage_counters.expires_at < now() THEN 1 ELSE usage_counters.count + 1 END, "
        "expires_at = CASE WHEN usage_counters.expires_at < now() THEN EXCLUDED.expires_at ELSE usage_counters.expires_at END "
        "RETURNING count"), {"b": bucket, "e": exp}).scalar_one()


def peek(session: Session, bucket: str) -> int:
    return session.execute(text("SELECT count FROM usage_counters WHERE bucket = :b AND expires_at >= now()"), {"b": bucket}).scalar() or 0


def put(session: Session, bucket: str, ttl: int) -> None:
    """設一個旗標（值為 1），已存在就延長期限"""
    exp = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    session.execute(text("INSERT INTO usage_counters (bucket, count, expires_at) VALUES (:b, 1, :e) "
                         "ON CONFLICT (bucket) DO UPDATE SET count = 1, expires_at = EXCLUDED.expires_at"), {"b": bucket, "e": exp})


def hour_ok(session: Session, bot: str, who: str, limit: int) -> bool:
    now = datetime.now(tz())
    return bump(session, f"hour:{bot}:{who}:{now:%Y%m%d%H}", 3600) <= limit


def day_ok(session: Session, bot: str, limit: int) -> bool:
    now = datetime.now(tz())
    return bump(session, f"day:{bot}:{now:%Y%m%d}", 2 * 86400) <= limit


def code_locked(session: Session, bot: str, ip: str, code: str) -> bool:
    """鎖定期間只接受這個來源 24 小時內成功用過的邀請碼"""
    return peek(session, f"codelock:{bot}:{ip}") > 0 and peek(session, f"codeok:{bot}:{ip}:{code}") == 0


def code_fail(session: Session, bot: str, ip: str) -> None:
    n = bump(session, f"badcode:{bot}:{ip}:{int(datetime.now(timezone.utc).timestamp()) // CODE_FAIL_WINDOW}", CODE_FAIL_WINDOW)
    if n >= CODE_FAIL_MAX:
        put(session, f"codelock:{bot}:{ip}", CODE_LOCK_SECONDS)


def code_ok(session: Session, bot: str, ip: str, code: str) -> None:
    put(session, f"codeok:{bot}:{ip}:{code}", 86400)
