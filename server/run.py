"""一個指令啟動伺服器（Linux、macOS、Windows 都一樣）：讀 server/.env → 更新資料庫結構 → 啟動 uvicorn。

  python run.py              正式執行（程式數依 WEB_CONCURRENCY）
  python run.py --dev        開發模式：改程式自動重啟，只開一個程式
  python run.py --no-migrate 不自動更新資料庫結構

監聽位址與埠在 .env 設 HOST（預設 127.0.0.1，只給本機的反向代理連）與 PORT（預設 8000）。
通常用 start.sh（Linux／macOS）或 start.bat（Windows）呼叫，它們會先準備好虛擬環境。
"""
import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        sys.exit(f"{name} 要是整數（目前是 {os.environ.get(name)!r}）")


def ensure_env_file() -> None:
    """第一次執行：從 .env.example 複製一份 .env，請使用者填好再啟動"""
    if (HERE / ".env").exists() or os.environ.get("DATABASE_URL"):
        return
    import shutil
    shutil.copyfile(HERE / ".env.example", HERE / ".env")
    sys.exit(f"已建立 {HERE / '.env'}：請填好 DATABASE_URL、JWT_SECRET 與模型金鑰後再執行一次")


def check_env() -> None:
    missing = [k for k in ("DATABASE_URL", "JWT_SECRET") if not os.environ.get(k, "").strip()]
    if missing:
        sys.exit("server/.env 還沒填：" + "、".join(missing))
    if len(os.environ["JWT_SECRET"].strip()) < 32:
        sys.exit("JWT_SECRET 至少要 32 個字元（可用 python -c \"import secrets; print(secrets.token_urlsafe(48))\" 產生）")
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        print("提醒：GEMINI_API_KEY 與 OPENAI_API_KEY 都沒填，問答與文件處理會失敗", file=sys.stderr)


def migrate() -> None:
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(HERE / "alembic.ini"))
    cfg.set_main_option("script_location", str(HERE / "db" / "migrations"))
    print("更新資料庫結構（alembic upgrade head）…", flush=True)
    command.upgrade(cfg, "head")


def main(argv=None) -> None:
    for st in (sys.stdout, sys.stderr):      # 輸出被導到檔案（例如 Windows 服務）時，系統編碼印不出的字改成跳脫，不讓啟動失敗
        try:
            st.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    if os.name == "nt" and not sys.flags.utf8_mode:
        # 繁中 Windows 的預設編碼是 cp950：第三方套件讀寫檔案、印 log 時遇到中文容易 UnicodeDecodeError。
        # 用 UTF-8 模式（python -X utf8）重新執行自己；子行程（多開）也繼承這個設定
        import subprocess
        os.environ["PYTHONUTF8"] = "1"
        try:
            sys.exit(subprocess.call([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), *(sys.argv[1:] if argv is None else argv)]))
        except KeyboardInterrupt:
            sys.exit(130)
    os.environ.setdefault("PYTHONUTF8", "1")
    if sys.version_info < (3, 12):
        sys.exit(f"需要 Python 3.12 以上（目前 {sys.version.split()[0]}）")
    p = argparse.ArgumentParser(prog="python run.py", description="啟動 RAG 問答伺服器")
    p.add_argument("--dev", action="store_true", help="開發模式：改程式自動重啟、只開一個程式")
    p.add_argument("--no-migrate", action="store_true", help="不自動更新資料庫結構")
    a = p.parse_args(argv)
    os.chdir(HERE)
    sys.path.insert(0, str(HERE))
    ensure_env_file()
    import db.session  # noqa: F401  讀 .env
    check_env()
    if not a.no_migrate:
        migrate()
    import uvicorn
    host, port = os.environ.get("HOST") or "127.0.0.1", env_int("PORT", 8000)
    workers = 1 if a.dev else max(1, env_int("WEB_CONCURRENCY", 1))
    print(f"啟動：http://{host}:{port}（{'開發模式' if a.dev else f'{workers} 個程式'}）", flush=True)
    uvicorn.run("app.main:app", host=host, port=port, workers=None if a.dev else workers, reload=a.dev, proxy_headers=False,
                reload_dirs=[str(HERE)] if a.dev else None)


if __name__ == "__main__":
    main()
