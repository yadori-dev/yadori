"""#45: 組を解く部品へ直接入力を渡して、規則を確かめる。

記録も埋め込みも判定も通さない。並び（前回のやりとりは名前付き、新しい発話は名前無し）と
判定の組と前回の番号を渡し、やりとりと問がどう出るかだけを見る。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from yadori.domain.evaluation import CannotDraft, Case, Exchange, Pair
from yadori.domain.memory import HowToRecall
from yadori.usecase.evaluation.resolve import (
    EMPTY_PREVIOUS,
    Placed,
    Previous,
    Resolving,
    Where,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
HOW = HowToRecall(recent_turns=2, found_limit=10, relevance_floor=0.15)


def _new(text: str, minute: int) -> Placed:
    return Placed(
        name=None,
        utterance=text,
        reply="はい",
        where=Where("/w", START + timedelta(minutes=minute)),
    )


def _old(name: str, text: str, minute: int) -> Placed:
    return Placed(
        name=name,
        utterance=text,
        reply="前の返事",
        where=Where("/w", START + timedelta(minutes=minute)),
    )


def _previous(placed: list[Placed], last_case: int) -> Previous:
    return Previous(
        exchanges=tuple(Exchange(one.name or "", one.utterance, one.reply) for one in placed),
        cases=(),
        last_exchange=len(placed),
        last_case=last_case,
    )


class TestResolving:
    def test_期待をまとめ真ん中は期待に残り期待の無い問は覚えさせる側へ戻る(self) -> None:
        placed = [_new(text, minute) for minute, text in enumerate("ABCDEFGH")]
        # C は A と B を指す（複数）。D は C を指す（連鎖）。H は G を指すが G は直近に入る。
        pairs = [Pair(2, 0), Pair(2, 1), Pair(3, 2), Pair(7, 6)]

        resolved = Resolving(HOW).resolve(EMPTY_PREVIOUS, placed, pairs)

        cases = {case.utterance: case for case in resolved.recall_eval.cases}
        exchanges = [one.utterance for one in resolved.recall_eval.exchanges]
        assert "C" not in cases and "C" in exchanges  # 連鎖の真ん中は期待に残る
        assert cases["D"].expected == ("e003",)  # C の名前
        assert "H" not in cases and "H" in exchanges  # 直近で外れた発話は覚えさせる側に戻る
        assert exchanges == ["A", "B", "C", "E", "F", "G", "H"]
        assert [case.name for case in resolved.recall_eval.cases] == ["c001"]
        assert resolved.last_exchange == 7 and resolved.last_case == 1
        assert all(case.confirmed is False for case in resolved.recall_eval.cases)

    def test_合わせた並びの末尾から直近を数え戻す前の並びで判定する(self) -> None:
        previous = [_old(f"e{n:03d}", f"前{n}", n) for n in range(1, 7)]
        placed = [*previous, _new("X", 10), _new("Y", 11), _new("Z", 12)]
        # X → e003（遠い）、Y → e004（遠い）、Z → e006（前回の末尾。合わせた並びの末尾二つに入る）
        pairs = [Pair(6, 2), Pair(7, 3), Pair(8, 5)]

        resolved = Resolving(HOW).resolve(_previous(previous, last_case=5), placed, pairs)

        assert [case.name for case in resolved.added.cases] == ["c006", "c007"]
        assert [case.utterance for case in resolved.added.cases] == ["X", "Y"]
        assert [one.name for one in resolved.added.exchanges] == ["e007"]
        assert resolved.added.exchanges[0].utterance == "Z"
        # 前回のやりとりはそのまま先頭に残り、新しい分が末尾に続く。
        assert [one.name for one in resolved.recall_eval.exchanges] == [
            f"e{n:03d}" for n in range(1, 8)
        ]
        assert resolved.last_exchange == 7 and resolved.last_case == 7

    def test_番号は前回の最後の番号から続き名前からは求めない(self) -> None:
        previous = [_old("e001", "前1", 1), _old("好きな名前", "前2", 2)]
        placed = [*previous, _new("X", 10), _new("Y", 11), _new("Z", 12)]
        before = Previous(
            exchanges=tuple(Exchange(one.name or "", one.utterance, one.reply) for one in previous),
            cases=(Case("c-renamed", "問", ("e001",), (), True, ()),),
            last_exchange=9,
            last_case=4,
        )

        resolved = Resolving(HOW).resolve(before, placed, [Pair(4, 0)])

        assert [one.name for one in resolved.added.exchanges] == ["e010", "e011"]
        assert [case.name for case in resolved.added.cases] == ["c005"]
        assert resolved.recall_eval.cases[0].name == "c-renamed"
        assert resolved.added.cases[0].expected == ("e001",)
        assert resolved.added.cases[0].overlap[0][0] == "e001"

    def test_前回の分が壊れていれば断り前回の問は変えない(self) -> None:
        previous = [_old("e001", "前1", 1)]
        broken = Previous(
            exchanges=(Exchange("e001", "前1", "前の返事"),),
            cases=(Case("c001", "問", ("e099",), (), True, ()),),
            last_exchange=1,
            last_case=1,
        )

        with pytest.raises(CannotDraft, match="e099"):
            _ = Resolving(HOW).resolve(broken, [*previous, _new("X", 10)], [])

    def test_前回のやりとりは後の発話にならず新しい発話どうしの組だけが問になる(self) -> None:
        previous = [_old("e001", "前1", 1), _old("e002", "前2", 2)]
        placed = [*previous, _new("X", 10), _new("Y", 11), _new("Z", 12)]

        resolved = Resolving(
            HowToRecall(recent_turns=0, found_limit=10, relevance_floor=0.15)
        ).resolve(_previous(previous, last_case=0), placed, [Pair(3, 2)])

        assert [case.utterance for case in resolved.added.cases] == ["Y"]
        assert resolved.added.cases[0].expected == ("e003",)  # X が e003 として覚えさせる側にある
        assert [one.utterance for one in resolved.added.exchanges] == ["X", "Z"]
