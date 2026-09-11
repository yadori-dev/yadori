"""インデックスが起動をまたいで使えることを確かめる。

埋め込みの値がプロセスごとに変わると、保存したインデックスを次の起動で読めない。
原文から作り直せるという前提（ADR-006）が崩れ、しかも動いている間は気づけ
ない。組み込みの `hash` を使うと実際にそうなる。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

READ_ONE = (
    "from yadori.adapter.embedding.characters import CharacterPairs;"
    "print([round(v, 6) for v in CharacterPairs().to_remember('トマトを植えました')])"
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


class TestCloseness:
    """近さが、長さ1で返さない埋め込みでも一を超えないことを確かめる。

    長さ1を前提にすると、そうでない実装へ替えたときに下限がどこでも効かなく
    なる。実際にそうなった。
    """

    def test_長さ1でない並びでも近さが一を超えない(self) -> None:
        from yadori.adapter.embedding import Closeness

        closeness = Closeness()
        long_one = (3.0, 4.0, 0.0)
        same_way = (30.0, 40.0, 0.0)

        assert closeness.between(long_one, same_way) == pytest.approx(1.0)
        assert closeness.between(long_one, (-3.0, -4.0, 0.0)) == pytest.approx(-1.0)
        assert closeness.between(long_one, (0.0, 0.0, 0.0)) == 0.0
