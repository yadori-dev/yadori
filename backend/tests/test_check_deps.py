"""層の依存の向きの検査が、実際に違反を捕まえることを確かめる。

ADR-004 の向きを守らせるのはこの検査だけなので、検査自身が働かなくなると
気づけない。壊れた形を渡して落ちることを見る。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_deps.py"


def build(root: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        path = root / "backend" / "src" / "yadori" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(body, encoding="utf-8")


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_内向きの依存は通る(tmp_path: Path) -> None:
    build(
        tmp_path,
        {
            "domain/memory/__init__.py": "",
            "usecase/conversation/__init__.py": "from yadori.domain import memory\n",
            "adapter/discord/__init__.py": "import yadori.usecase.conversation\n",
        },
    )

    result = run(tmp_path)

    assert result.returncode == 0, result.stderr


def test_外向きの依存は落ちる(tmp_path: Path) -> None:
    build(tmp_path, {"domain/memory/__init__.py": "from yadori.adapter import discord\n"})

    result = run(tmp_path)

    assert result.returncode == 1
    assert "domain が adapter に依存している" in result.stderr


def test_同じ層の中の依存は通る(tmp_path: Path) -> None:
    build(
        tmp_path,
        {
            "domain/memory/__init__.py": "",
            "domain/dream/__init__.py": "from yadori.domain import memory\n",
        },
    )

    result = run(tmp_path)

    assert result.returncode == 0, result.stderr


def test_usecase_は_adapter_を知らない(tmp_path: Path) -> None:
    build(tmp_path, {"usecase/conversation/__init__.py": "import yadori.adapter.store\n"})

    result = run(tmp_path)

    assert result.returncode == 1
    assert "usecase が adapter に依存している" in result.stderr
