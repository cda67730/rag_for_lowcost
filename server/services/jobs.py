"""背景工作的歸屬與存活：多開程式（WEB_CONCURRENCY）時，只把「真的沒人在做」的工作標成失敗。

- 每個工作記下是哪個伺服器執行個體建立、執行的（worker）；執行期間每分鐘更新一次 updated_at（心跳）。
- 啟動時：同一台機器上、已經不在的執行個體留下的工作 → 失敗（伺服器重啟後馬上可以重試）。
- 每 5 分鐘：任何工作超過 15 分鐘沒有心跳 → 失敗（別台機器當掉、容器被砍掉時的保底）。
"""
import asyncio
import logging
import multiprocessing
import os
import socket
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from db.models import Document, Job

log = logging.getLogger("jobs")
BEAT_SECONDS = 60
STALE = timedelta(minutes=15)
CHECK_SECONDS = 300
LIVE = ("queued", "running")


def _start_time(pid: int) -> str | None:
    """行程的啟動時間（Linux 讀 /proc、Windows 問系統）；行程不在或拿不到回 None"""
    if sys.platform == "win32":
        return _win_start_time(pid)
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as f:
            return f.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _win_start_time(pid: int) -> str | None:
    """Windows：OpenProcess＋GetProcessTimes 取建立時間。注意 Windows 的 os.kill(pid, 0) 會直接結束那個行程，不能拿來探測"""
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype, k.OpenProcess.argtypes = wintypes.HANDLE, (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    h = k.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        code = wintypes.DWORD()
        if not k.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:     # 259＝STILL_ACTIVE
            return None
        t = [wintypes.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(h, *(ctypes.byref(x) for x in t)):
            return None
        return str((t[0].dwHighDateTime << 32) | t[0].dwLowDateTime)
    finally:
        k.CloseHandle(h)


def instance_id() -> str:
    """主機:主行程 pid:主行程啟動時間。uvicorn 多開時各個子行程共用主行程，算同一個執行個體"""
    parent = multiprocessing.parent_process()
    pid = parent.pid if parent is not None and parent.pid else os.getpid()
    return f"{socket.gethostname()}:{pid}:{_start_time(pid) or ''}"


INSTANCE = instance_id()


def alive(worker: str) -> bool | None:
    """True＝還在、False＝確定不在了、None＝不是這台機器，無法判斷"""
    try:
        host, pid, start = worker.rsplit(":", 2)
        pid_n = int(pid)
    except ValueError:
        return False                         # 舊資料（沒有記 worker）
    if host != socket.gethostname():
        return None
    now = _start_time(pid_n)
    if now is not None:
        return not start or now == start     # pid 被別的行程重用時啟動時間會不同
    if sys.platform == "win32" or os.path.exists("/proc"):
        return False                         # 這兩種系統拿得到啟動時間：拿不到就是行程不在
    try:                                     # 其他系統（macOS）：只能確認 pid 存在；signal 0 不會影響行程
        os.kill(pid_n, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _fail(s: Session, jobs: list[Job], msg: str, doc_msg: str) -> int:
    now = datetime.now(timezone.utc)
    for j in jobs:
        j.status, j.message, j.finished_at = "failed", msg, now
        if j.kind == "ingest" and j.target:
            try:
                d = s.get(Document, __import__("uuid").UUID(j.target))
            except ValueError:
                d = None
            if d is not None and d.status == "processing":
                d.status, d.error = "failed", doc_msg
    s.commit()
    return len(jobs)


def recover(s: Session, startup: bool = False) -> int:
    """把沒人在做的工作標成失敗，回傳筆數。startup：另外檢查同一台機器上已經結束的執行個體"""
    live = s.scalars(select(Job).where(Job.status.in_(LIVE))).all()
    cutoff = datetime.now(timezone.utc) - STALE
    dead = [j for j in live if j.updated_at < cutoff or (startup and j.worker != INSTANCE and alive(j.worker) is False)]
    return _fail(s, dead, "伺服器重新啟動或工作中斷，請重試", "伺服器重新啟動或處理中斷，請重新處理") if dead else 0


async def run(factory, job_id, fn, *args) -> None:
    """執行背景工作，期間每分鐘更新心跳；工作本身的成功失敗由 fn 自己寫"""
    beat = asyncio.create_task(_beat(factory, job_id))
    try:
        await fn(*args)
    finally:
        beat.cancel()


async def _beat(factory, job_id, every: float | None = None) -> None:
    while True:
        await asyncio.sleep(every or BEAT_SECONDS)
        try:
            await asyncio.to_thread(_touch, factory, job_id)
        except Exception:
            log.exception("更新工作心跳失敗")


def _touch(factory, job_id) -> None:
    with factory() as s:
        s.execute(update(Job).where(Job.id == job_id, Job.status.in_(LIVE)).values(updated_at=datetime.now(timezone.utc)))
        s.commit()


async def monitor(factory, every: float | None = None) -> None:
    """定期找出沒有心跳的工作"""
    while True:
        await asyncio.sleep(every or CHECK_SECONDS)
        try:
            n = await asyncio.to_thread(_recover_once, factory)
            if n:
                log.warning("%d 個工作超過 %d 分鐘沒有回報進度，標為失敗", n, STALE.seconds // 60)
        except Exception:
            log.exception("檢查背景工作失敗")


def _recover_once(factory) -> int:
    with factory() as s:
        return recover(s)


