"""Шари не залежать «вгору»: domain не знає про Telegram, ui — про сервіси, сервіси — про обробники."""
import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "halloween_bot"

FORBIDDEN = {
    "domain":   {"aiogram", "services", "handlers", "ui", "app"},
    "ui":       {"services", "handlers", "app"},
    "services": {"handlers", "app"},
    "storage":  {"aiogram", "domain", "ui", "services", "handlers", "app"},
    "config":   {"aiogram", "storage", "domain", "ui", "services", "handlers", "app"},
}


def imports_of(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:                      # відносний імпорт: ..services.x → services
                names.add(mod.split(".")[0] if mod else "")
            else:
                names.add(mod.split(".")[0])
    return names


def test_layers_do_not_import_upwards():
    problems = []
    for layer, banned in FORBIDDEN.items():
        target = PKG / layer
        files = list(target.rglob("*.py")) if target.is_dir() else [PKG / f"{layer}.py"]
        for f in files:
            bad = imports_of(f) & banned
            if bad:
                problems.append(f"{f.relative_to(PKG)} → {sorted(bad)}")
    assert not problems, "\n".join(problems)


def test_handlers_are_thin():
    """Обробники не мають розростатися: логіка — у services/domain."""
    for f in (PKG / "handlers").glob("*.py"):
        assert len(f.read_text(encoding="utf-8").splitlines()) < 200, f.name


def test_no_module_is_a_monolith():
    for f in PKG.rglob("*.py"):
        assert len(f.read_text(encoding="utf-8").splitlines()) < 300, f.relative_to(PKG)
