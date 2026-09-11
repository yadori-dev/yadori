#!/usr/bin/env python3
"""責務がクラスに閉じているかを検査する。

モジュールの直下に関数を置くことを禁じる。名前の付いた関数が並ぶだけの
モジュールは、どれが入口でどれが下請けかを読めない。責務をクラスへ閉じ、
入口から段を下げて読める形にする。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import final

DEFAULT_ROOT = Path(__file__).resolve().parent.parent


@final
class ModuleFunctions:
    """モジュールの直下に置かれた関数を見つける。"""

    def __init__(self, root: Path) -> None:
        self._root: Path = root
        self._package: Path = root / "backend" / "src"

    def offences(self) -> list[str]:
        found: list[str] = []
        for path in sorted(self._package.rglob("*.py")):
            found.extend(self._in(path))
        return found

    def _in(self, path: Path) -> list[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(self._root)
        return [
            f"{relative}:{node.lineno}: {node.name} がモジュールの直下にある。クラスへ閉じる"
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ]


@final
class Check:
    def __init__(self, argv: list[str]) -> None:
        self._root: Path = Path(argv[1]).resolve() if len(argv) > 1 else DEFAULT_ROOT

    def run(self) -> int:
        offences = ModuleFunctions(self._root).offences()
        if offences:
            print("責務がクラスに閉じていません:", file=sys.stderr)
            for line in offences:
                print(f"  {line}", file=sys.stderr)
            return 1
        print("責務の閉じ方: 問題なし")
        return 0


if __name__ == "__main__":
    sys.exit(Check(sys.argv).run())
