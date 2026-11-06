"""Guards for the deployment files (Dockerfile, fly.toml, render.yaml) and the release."""

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server" / "flmp_server"
STDLIB = set(sys.stdlib_module_names)


def _top_imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_server_needs_only_websockets_and_core():
    # The server image installs websockets only: no MIDI libraries.
    used = set().union(*(_top_imports(p) for p in SERVER.glob("*.py")))
    assert used - STDLIB - {"__future__"} <= {"websockets", "flmp_core", "flmp_server"}


def test_core_used_by_the_server_is_stdlib_only():
    used = set().union(*(_top_imports(p) for p in (ROOT / "fl_script" / "flmp_core").glob("*.py")))
    assert used - STDLIB - {"__future__"} <= {"flmp_core"}


def _locked_version(name: str) -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return next(p["version"] for p in lock["package"] if p["name"] == name)


def test_server_requirements_are_pinned_and_hashed():
    text = (ROOT / "server" / "requirements.txt").read_text(encoding="utf-8")
    reqs = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", text, flags=re.M)
    assert reqs == [("websockets", _locked_version("websockets"))]
    assert "--hash=sha256:" in text


def test_dockerfile_is_minimal_and_unprivileged():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--require-hashes" in text
    assert "server/requirements.txt" in text
    assert re.search(r"^USER\s+(?!root)\S+", text, flags=re.M)
    assert "mido" not in text and "rtmidi" not in text
    assert re.search(r"^FROM python:3\.12-slim", text, flags=re.M)  # hashes are for cp312
    assert re.search(r"^FROM \S+@sha256:[0-9a-f]{64}$", text, flags=re.M)  # immutable base


def test_fly_config_trusts_only_fly_proxy():
    fly = tomllib.loads((ROOT / "fly.toml").read_text(encoding="utf-8"))
    assert fly["env"]["FLMP_TRUST_PROXY"] == "1"  # Fly sets Fly-Client-IP itself
    service = fly["http_service"]
    assert service["internal_port"] == int(fly["env"]["PORT"])
    assert service["force_https"] is True
    assert any(c["path"] == "/healthz" for c in service["checks"])


def test_render_config_trusts_no_header():
    lines = (ROOT / "render.yaml").read_text(encoding="utf-8").splitlines()
    text = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    assert "FLMP_TRUST_PROXY" not in text  # Render's X-Forwarded-For can be spoofed
    assert re.search(r"healthCheckPath:\s*/healthz", text)
    assert re.search(r"plan:\s*free", text)
    assert re.search(r"runtime:\s*docker", text)


def test_versions_agree():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    script = (ROOT / "fl_script" / "device_FLMultiplayer.py").read_text(encoding="utf-8")
    assert project["version"] == "0.2.0"
    assert 'VERSION = "0.2.0"' in script
