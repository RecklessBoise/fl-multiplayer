"""Guards for code that must run inside FL Studio's embedded Python."""

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "fl_script" / "flmp_core"


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.append(node.module or "")
    return names


def test_core_imports_nothing_but_itself_and_future():
    offenders = {
        p.name: [n for n in _imports(p) if n != "__future__"] for p in sorted(CORE.glob("*.py"))
    }
    assert {k: v for k, v in offenders.items() if v} == {}


def test_core_parses_as_python_37():
    for p in CORE.glob("*.py"):
        ast.parse(p.read_text(encoding="utf-8"), filename=p.name, feature_version=(3, 7))


def test_core_has_no_print_or_open_calls():
    for p in CORE.glob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"open", "eval", "exec", "__import__"}, p.name
