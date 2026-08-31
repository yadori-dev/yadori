"""追記のテストで使う架空の記録と、下書きを作って足す手順の呼び方。

利用者の実際の記録を使わない。文字の埋め込み（`CharacterPairs`）と下書き用の条件で、
言い直しの組が候補に上がり、無関係な文が上がらないことを、値を見て選んだ文である。
"""

from __future__ import annotations

import io
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import final

from tests.records import (
    WORKSPACE,
    FailingJudge,
    FixedJudge,
    claude_code_lines,
    write,
)
from yadori.adapter.embedding import CharacterPairs
from yadori.domain.evaluation import Judge
from yadori.domain.memory import Embeddings, HowToRecall, Provenance, Vector
from yadori.infrastructure.draft import Drafter

TOMATO = "ベランダにトマトの苗を植えました"
TOMATO_AGAIN = "ベランダのトマトの苗を植えた話をもう一度聞かせてください"
TOMATO_THIRD = "ベランダのトマトの苗はその後どうなりましたか"
TAX = "住民税の納付書が届きました"
TAX_AGAIN = "住民税の納付書はもう払いましたか"
MOVIE = "昨日見た古い映画の題名を思い出せません"
MOVIE_AGAIN = "古い映画の題名は何でしたか"
BOOK = "図書館で小説を三冊借りました"
BOOK_AGAIN = "図書館で借りた小説は読み終わりましたか"
GARDEN = "庭の雑草を抜いて肥料をまきました"
PIANO = "ピアノの発表会の曲を決めました"
# 他のどの文とも語が重ならず、候補が一つも上がらない。
ALONE = "北海道旅行の宿を予約した"
NOD = "いいよ"

# 最初の下書きの材料。TOMATO_AGAIN → TOMATO、TAX_AGAIN → TAX が問になる。
FIRST = [
    (TOMATO, "いいですね"),
    (TAX, "期限を確かめましょう"),
    (MOVIE, "手掛かりはありますか"),
    (BOOK, "何を借りましたか"),
    (GARDEN, "お疲れさまです"),
    (PIANO, "楽しみですね"),
]
FIRST_LATER = [(TOMATO_AGAIN, "トマトの件ですね"), (TAX_AGAIN, "納付書の件ですね")]
FIRST_JUDGE = {TOMATO_AGAIN: [TOMATO], TAX_AGAIN: [TAX]}
# 追記用の条件。直近二往復、候補十件、下限 0.15。
HOW = HowToRecall(recent_turns=2, found_limit=10, relevance_floor=0.15)


@final
class Relabeled:
    """出自だけを変えた文字の埋め込み。引き方が違う下書きへの追記を試すために使う。"""

    def __init__(self, provenance: Provenance) -> None:
        self._inner: CharacterPairs = CharacterPairs()
        self._provenance: Provenance = provenance

    @property
    def provenance(self) -> Provenance:
        return self._provenance

    @property
    def name(self) -> str:
        return self._provenance.index_name

    def of(self, text: str) -> Vector:
        return self._inner.of(text)


def first_records(place: Path) -> Path:
    """最初の下書きの記録。二つのセッション。"""
    _ = write(place, "first.jsonl", claude_code_lines("s1", WORKSPACE, FIRST))
    _ = write(
        place, "later.jsonl", claude_code_lines("s2", WORKSPACE, FIRST_LATER, first_minute=100)
    )
    return place


def drafted(
    place: Path,
    out: Path,
    judge: Judge | None = None,
    *,
    append: bool = False,
    embeddings: Embeddings | None = None,
    how: HowToRecall | None = None,
    places: Sequence[Path] | None = None,
) -> tuple[int, str, str]:
    """入口の一つ手前から呼ぶ。終了状態、標準出力、標準エラー。"""
    import sys

    written, errors = io.StringIO(), io.StringIO()
    real = sys.stderr
    sys.stderr = errors
    try:
        code = Drafter(
            places or [place],
            out,
            append=append,
            judge=judge or FixedJudge(FIRST_JUDGE),
            embeddings=embeddings or CharacterPairs(),
            how=how or HOW,
            writing=written,
        ).run()
    finally:
        sys.stderr = real
    return code, written.getvalue(), errors.getvalue()


def first_draft(tmp_path: Path) -> tuple[Path, Path]:
    """記録を置いて最初の下書きを作る。記録のディレクトリと下書きのパスを返す。"""
    place = first_records(tmp_path / "records")
    out = tmp_path / "out" / "draft.toml"
    out.parent.mkdir()
    code, _, errors = drafted(place, out)
    assert code == 0, errors
    return place, out


def read(out: Path) -> dict[str, object]:
    return tomllib.loads(out.read_text(encoding="utf-8"))


def covered_of(out: Path) -> dict[str, object]:
    table = read(out).get("covered")
    assert isinstance(table, dict)
    return table  # pyright: ignore[reportUnknownVariableType]


def utterances(out: Path, key: str) -> list[str]:
    rows = read(out).get(key, [])
    assert isinstance(rows, list)
    found: list[str] = []
    for row in rows:  # pyright: ignore[reportUnknownVariableType]
        assert isinstance(row, dict)
        value = row.get("utterance")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        assert isinstance(value, str)
        found.append(value)
    return found


def names(out: Path, key: str) -> list[str]:
    rows = read(out).get(key, [])
    assert isinstance(rows, list)
    found: list[str] = []
    for row in rows:  # pyright: ignore[reportUnknownVariableType]
        assert isinstance(row, dict)
        value = row.get("name")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        assert isinstance(value, str)
        found.append(value)
    return found


def outside_covered(text: str) -> str:
    """`[covered]` の塊を除いた文字の並び。追記で変わってはいけない部分。"""
    lines = text.splitlines(keepends=True)
    start = next(index for index, line in enumerate(lines) if line.strip() == "[covered]")
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("[")),
        len(lines),
    )
    return "".join(lines[:start] + lines[end:])


def failing(reason: str) -> FailingJudge:
    return FailingJudge(reason)


def judge_of(pointing: Mapping[str, Sequence[str]]) -> FixedJudge:
    return FixedJudge({**FIRST_JUDGE, **pointing})
