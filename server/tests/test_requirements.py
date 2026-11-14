"""requirements.txt 要和 pyproject.toml 一致：每個直接相依都有固定版本，而且版本落在 pyproject 寫的範圍內。"""
import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent


def pins(name: str) -> dict:
    out = {}
    for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-r"):
            continue
        r = Requirement(line)
        (spec,) = r.specifier
        assert spec.operator == "==", line            # 一律固定版本
        out[canonicalize_name(r.name)] = spec.version
    return out


def test_requirements_match_pyproject():
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    run, dev = pins("requirements.txt"), pins("requirements-dev.txt")
    assert not set(run) & set(dev)                     # dev 只放測試工具，執行用的靠 -r 引進
    for want, have in ((proj["dependencies"], run), (proj["optional-dependencies"]["dev"], {**run, **dev})):
        for d in want:
            r = Requirement(d)
            name = canonicalize_name(r.name)
            assert name in have, f"{r.name} 沒有固定版本"
            assert r.specifier.contains(have[name]), f"{r.name}=={have[name]} 不在 pyproject 的範圍 {r.specifier}"
    assert re.search(r'^uvloop==\S+ ; sys_platform != "win32"', (ROOT / "requirements.txt").read_text(), re.M)   # Windows 不能裝
