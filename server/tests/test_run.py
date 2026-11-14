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


def test_files_read_with_system_encoding_are_ascii():
    """這些檔案會被第三方工具用「系統編碼」讀（繁中 Windows 是 cp950），內容有中文就會 UnicodeDecodeError：一律只用 ASCII"""
    for name in ("alembic.ini", "requirements.txt", "requirements-dev.txt", "start.bat"):
        data = (ROOT / name).read_bytes()
        bad = [i for i, b in enumerate(data) if b > 127]
        assert not bad, f"{name} 第 {data[:bad[0]].count(b'\\n') + 1} 行有非 ASCII 字元"
    assert b"\r\n" in (ROOT / "start.bat").read_bytes()


def test_cp950_locale_can_read_config(monkeypatch):
    """模擬繁中 Windows：用 cp950 讀 alembic.ini 與 requirements 不會失敗；.env 存成 UTF-8（含 BOM）或 Big5 都讀得到"""
    import configparser
    configparser.ConfigParser().read(ROOT / "alembic.ini", encoding="cp950")
    for name in ("requirements.txt", "requirements-dev.txt"):
        (ROOT / name).read_bytes().decode("cp950")


def test_env_file_encodings(tmp_path, monkeypatch):
    import locale
    import os
    import db.session as S
    for i, (enc, data) in enumerate([("utf-8-sig", "# 註解\nRAG_E{i}=值{i}\n"), ("utf-8", "# 註解\nRAG_E{i}=值{i}\n"), ("cp950", "# 註解\nRAG_E{i}=值{i}\n")]):
        p = tmp_path / f"e{i}.env"
        p.write_bytes(data.format(i=i).encode(enc))
        monkeypatch.delenv(f"RAG_E{i}", raising=False)
        if enc == "cp950":
            monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "cp950")
        S._load_env_file(p)
        assert os.environ.get(f"RAG_E{i}") == f"值{i}", enc
        monkeypatch.delenv(f"RAG_E{i}", raising=False)


def test_windows_reexec_in_utf8_mode(monkeypatch):
    """Windows 沒開 UTF-8 模式時，run.py 用 python -X utf8 重新執行自己，參數照傳、回傳碼照回"""
    import os
    import subprocess
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(subprocess, "call", lambda cmd: calls.append(cmd) or 3)
    fake_os = SimpleNamespace(name="nt", environ={}, path=os.path, chdir=os.chdir)
    fake_sys = SimpleNamespace(flags=SimpleNamespace(utf8_mode=False), executable="C:\\py\\python.exe", exit=sys.exit, stdout=sys.stdout,
                               stderr=sys.stderr, version_info=sys.version_info, argv=["run.py", "--dev"], path=sys.path)
    monkeypatch.setattr(run, "os", fake_os)
    monkeypatch.setattr(run, "sys", fake_sys)
    with pytest.raises(SystemExit) as e:
        run.main(["--dev"])
    assert e.value.code == 3 and fake_os.environ["PYTHONUTF8"] == "1"
    assert calls == [["C:\\py\\python.exe", "-X", "utf8", str(Path(run.__file__).resolve()), "--dev"]]
