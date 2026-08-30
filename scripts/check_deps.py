#!/usr/bin/env python3
"""層の依存の向きを検査する。

infrastructure → adapter → usecase → domain の内向き一方向だけを許す（ADR-004）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent

# 各層が依存してよい層。自分自身は常に許す。
ALLOWED: dict[str, set[str]] = {
    "domain": set(),
    "usecase": {"domain"},
    "adapter": {"usecase", "domain"},
    "infrastructure": {"adapter", "usecase", "domain"},
}


def layer_of(path: Path, package: Path) -> str | None:
    parts = path.relative_to(package).parts
    return parts[0] if parts and parts[0] in ALLOWED else None


def imported_layers(tree: ast.AST) -> set[tuple[str, int]]:
    found: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for name in names:
            head = name.split(".")
            if len(head) >= 2 and head[0] == "yadori" and head[1] in ALLOWED:
                found.add((head[1], getattr(node, "lineno", 0)))
    return found


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else DEFAULT_ROOT
    package = root / "backend" / "src" / "yadori"

    violations: list[str] = []
    for path in sorted(package.rglob("*.py")):
        layer = layer_of(path, package)
        if layer is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for imported, lineno in sorted(imported_layers(tree)):
            if imported == layer or imported in ALLOWED[layer]:
                continue
            rel = path.relative_to(root)
            violations.append(f"{rel}:{lineno}: {layer} が {imported} に依存している")

    if violations:
        print("層の依存の向きに違反があります（ADR-004）:", file=sys.stderr)
        for line in violations:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("層の依存の向き: 問題なし")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
