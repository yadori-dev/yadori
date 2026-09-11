"""責務の閉じ方の検査が、実際にモジュール直下の関数を捕まえることを確かめる。

この規則は人が読んで守るものなので、検査が働かなくなると静かに崩れる。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_classes.py"


class TestCheckClasses:
    def _build(self, root: Path, body: str) -> None:
        path = root / "backend" / "src" / "yadori" / "example.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(body, encoding="utf-8")

    def _run(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_クラスに閉じていれば通る(self, tmp_path: Path) -> None:
        self._build(tmp_path, "class Greeting:\n    def say(self) -> str:\n        return 'ほい'\n")

        result = self._run(tmp_path)

        assert result.returncode == 0, result.stderr

    def test_モジュール直下の関数は落ちる(self, tmp_path: Path) -> None:
        self._build(tmp_path, "def say() -> str:\n    return 'ほい'\n")

        result = self._run(tmp_path)

        assert result.returncode == 1
        assert "say がモジュールの直下にある" in result.stderr

    def test_非同期の関数も落ちる(self, tmp_path: Path) -> None:
        self._build(tmp_path, "async def say() -> str:\n    return 'ほい'\n")

        result = self._run(tmp_path)

        assert result.returncode == 1
        assert "say がモジュールの直下にある" in result.stderr
