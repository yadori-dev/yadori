"""索引が起動をまたいで使えることを確かめる。

埋め込みの値がプロセスごとに変わると、保存した索引を次の起動で読めない。
原文から作り直せるという前提（ADR-006）が崩れ、しかも動いている間は気づけ
ない。組み込みの `hash` を使うと実際にそうなる。
"""

from __future__ import annotations

import subprocess
import sys

READ_ONE = (
    "from yadori.adapter.embedding.characters import CharacterPairs;"
    "print([round(v, 6) for v in CharacterPairs().of('トマトを植えました')])"
)


def _values(seed: str) -> str:
    return subprocess.run(
        [sys.executable, "-c", READ_ONE],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
    ).stdout


def test_埋め込みが起動をまたいで同じ値になる() -> None:
    assert _values("0") == _values("99") != ""
