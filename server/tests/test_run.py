"""啟動程式 run.py 的檢查，以及 server/.env 的讀取（已設定的環境變數優先）。"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import run  # noqa: E402


def test_ensure_env_file_copies_example(tmp_path, monkeypatch):
    (tmp_path / ".env.example").write_text("DATABASE_URL=\n", encoding="utf-8")
    monkeypatch.setattr(run, "HERE", tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as e:
        run.ensure_env_file()
    assert "已建立" in str(e.value) and (tmp_path / ".env").read_text(encoding="utf-8") == "DATABASE_URL=\n"
    run.ensure_env_file()                                  # 已經有 .env：不動
    (tmp_path / ".env").unlink()
    monkeypatch.setenv("DATABASE_URL", "x")                # Docker／systemd 直接給環境變數：不需要 .env
    run.ensure_env_file()
    assert not (tmp_path / ".env").exists()


@pytest.mark.parametrize("env,msg", [({"DATABASE_URL": "", "JWT_SECRET": "k" * 40}, "DATABASE_URL"),
                                     ({"DATABASE_URL": "x", "JWT_SECRET": ""}, "JWT_SECRET"),
                                     ({"DATABASE_URL": "x", "JWT_SECRET": "short"}, "32 個字元")])
def test_check_env(monkeypatch, env, msg):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(SystemExit) as e:
        run.check_env()
    assert msg in str(e.value)


def test_check_env_ok_and_key_warning(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "x"); monkeypatch.setenv("JWT_SECRET", "k" * 40)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False); monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    run.check_env()
    assert "都沒填" in capsys.readouterr().err
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    run.check_env()


def test_dotenv_loaded_without_overriding(tmp_path):
    """db.session 會讀 server/.env；同名的環境變數已經有值時以環境變數為準"""
    code = ("import os, db.session; print(os.environ.get('RAG_T1'), os.environ.get('RAG_T2'))")
    env_file = ROOT / ".env"
    backup = env_file.read_bytes() if env_file.exists() else None
    try:
        env_file.write_text("RAG_T1=from_file\nRAG_T2=from_file\n", encoding="utf-8")
        import os
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                             env={**os.environ, "RAG_T2": "from_env"}).stdout.split()
        assert out == ["from_file", "from_env"]
    finally:
        if backup is None:
            env_file.unlink()
        else:
            env_file.write_bytes(backup)
