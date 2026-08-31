"""INC-030 の結合テスト。設計の境界と、その構造を選んだ理由を確かめる。"""

from __future__ import annotations

import io
import sys
import tomllib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import final

import pytest

from tests.records import (
    OTHER_WORKSPACE,
    WORKSPACE,
    FailingJudge,
    FixedJudge,
    claude_code_lines,
    claude_code_noise,
    codex_lines,
    names_of,
    rows_of,
    write,
)
from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.evaluation import (
    ClaudeCodeJudge,
    ClaudeCodeRecords,
    CodexRecords,
    DraftFile,
    EvalFile,
)
from yadori.adapter.store import InMemoryMemories
from yadori.domain.evaluation import (
    CannotDraft,
    CannotMeasure,
    Case,
    Exchange,
    RecallEval,
    Recorded,
)
from yadori.domain.memory import HowToRecall
from yadori.infrastructure.draft import Drafter
from yadori.infrastructure.entry import USAGE, Entry
from yadori.usecase.evaluation import Drafting, Measuring

A = "ベランダにトマトの苗を植えました"
B = "水やりは朝と夕方どちらがいいですか"
C = "植物の世話について教えてください"
D = "住民税の納付書が届きました"
E = "図書館で小説を三冊借りました"
NOD = "いいよ"
HOW = HowToRecall(recent_turns=1, found_limit=5, relevance_floor=0.21)

TURNS = [
    (A, "いいですね"),
    (NOD, "はい"),
    (B, "朝です"),
    (D, "期限をお忘れなく"),
    (E, "楽しみですね"),
]
# 別のセッションで前の話題を指す発話。同じセッションで直前を指す組は件にならない。
LATER = [(C, "トマトの件ですね")]
FILLERS = [(f"別の話題その{n}について相談です", "はい") for n in range(7)]


def _same(one: Recorded) -> tuple[str, str, datetime, str]:
    return (one.utterance, one.reply, one.at, one.workspace)


class TestIT030001:
    def test_IT_030_001_形式が違っても同じ形になり雑音と飛ばす基準が形式ごとに閉じる(
        self, tmp_path: Path
    ) -> None:
        place = tmp_path / "records"
        _ = write(
            place,
            "claude.jsonl",
            claude_code_lines("s1", WORKSPACE, TURNS)
            + claude_code_noise("s1", WORKSPACE, minute=50),
        )
        _ = write(place, "codex.jsonl", codex_lines("s2", WORKSPACE, TURNS))
        _ = write(place, "later.jsonl", claude_code_lines("s9", WORKSPACE, LATER, first_minute=90))
        _ = write(
            place, "broken.jsonl", claude_code_lines("s3", WORKSPACE, TURNS[:1]) + "{broken\n"
        )
        _ = write(place, "unknown.jsonl", '{"hello": "world"}\n')
        out = tmp_path / "draft.toml"

        from_claude = ClaudeCodeRecords().read(place / "claude.jsonl")
        from_codex = CodexRecords().read(place / "codex.jsonl")
        draft = Drafting(
            [ClaudeCodeRecords(), CodexRecords()], FixedJudge({C: [A]}), DraftFile(), HOW
        ).run([place], out)

        assert [_same(one) for one in from_claude] == [_same(one) for one in from_codex]
        assert not any("<" in one.utterance for one in from_claude)
        # 短い相槌は読み手からは返り、中身が無いと自分で答える。除くのは手順の側。
        nods = [one for one in from_claude if one.utterance == NOD]
        assert nods and not nods[0].has_substance()
        assert draft.skipped_files == 2


class TestIT030002:
    def test_IT_030_002_判定は作業場所ごとにその作業場所の発話だけを受け取る(
        self, tmp_path: Path
    ) -> None:
        place = tmp_path / "records"
        _ = write(place, "a.jsonl", claude_code_lines("s1", WORKSPACE, TURNS))
        _ = write(place, "c.jsonl", claude_code_lines("s9", WORKSPACE, LATER, first_minute=90))
        _ = write(
            place,
            "b.jsonl",
            codex_lines(
                "s2",
                OTHER_WORKSPACE,
                [("会議の議事録をまとめてください", "はい"), ("議事録の宛先は誰ですか", "はい")],
                first_minute=100,
            ),
        )
        judge = FixedJudge({C: [A]})

        _ = Drafting([ClaudeCodeRecords(), CodexRecords()], judge, DraftFile(), HOW).run(
            [place], tmp_path / "d.toml"
        )

        assert len(judge.calls) == 2
        assert sorted(len(call) for call in judge.calls) == [2, 5]
        assert not (set(judge.calls[0]) & set(judge.calls[1]))
        assert {A, B, C, D, E} in (set(judge.calls[0]), set(judge.calls[1]))

    def test_IT_030_002_判定の失敗は握られずに届き下書きは書かれない(self, tmp_path: Path) -> None:
        place = tmp_path / "records"
        _ = write(place, "a.jsonl", claude_code_lines("s1", WORKSPACE, TURNS))
        out = tmp_path / "d.toml"

        with pytest.raises(CannotDraft, match="上限"):
            _ = Drafting([ClaudeCodeRecords()], FailingJudge("上限に当たった"), DraftFile()).run(
                [place], out
            )

        assert not out.exists()


class TestIT030003:
    def test_IT_030_003_判定の結果をそのまま使わず測れる形へ解いてから書く(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "fastembed", None)
        place = tmp_path / "records"
        _ = write(place, "a.jsonl", claude_code_lines("s1", WORKSPACE, TURNS))
        _ = write(place, "c.jsonl", claude_code_lines("s9", WORKSPACE, LATER, first_minute=90))
        _ = write(place, "dup.jsonl", codex_lines("s2", WORKSPACE, [(A, "重複")], first_minute=100))
        _ = write(place, "late.jsonl", codex_lines("s3", WORKSPACE, FILLERS, first_minute=200))
        out = tmp_path / "d.toml"
        # C は A と B を指す（複数）。B は A を指す（連鎖）が同じセッションの直前なので組にならず、
        # B は覚えさせる側に残る。最後の話題は直近に入る期待を指す。
        judge = FixedJudge({C: [A, B], B: [A], FILLERS[6][0]: [FILLERS[0][0]]})

        # 既定の直近往復数（六）で解く。FILLERS[0] は残る並びの末尾六つに入る。
        _ = Drafting([ClaudeCodeRecords(), CodexRecords()], judge, DraftFile()).run([place], out)

        loaded = EvalFile(out).read()
        utterances = [one.utterance for one in loaded.exchanges]
        assert utterances.count(A) == 1
        cases = {case.utterance: case for case in loaded.cases}
        assert set(cases) == {C}
        assert len(cases[C].expected) == 2
        assert B in utterances
        assert len({one.name for one in loaded.exchanges}) == len(loaded.exchanges)
        # 語の重なりの度合いは人が読む欄で、測る側の読み手は読まない。ファイルの中で確かめる。
        written_cases = rows_of(tomllib.loads(out.read_text(encoding="utf-8")), "case")
        assert set(names_of(written_cases[0], "overlap")) == set(cases[C].expected)
        confirmed = RecallEval(
            within=loaded.within,
            exchanges=loaded.exchanges,
            cases=tuple(
                Case(one.name, one.utterance, one.expected, one.forbidden, True, one.overlap)
                for one in loaded.cases
            ),
        )
        measured = Measuring(confirmed, InMemoryMemories, CharacterPairs()).at(HOW)
        assert measured.total == 1


class TestIT030004:
    def _measure(self, recall_eval: RecallEval) -> None:
        _ = Measuring(recall_eval, InMemoryMemories, CharacterPairs()).at(HOW)

    def _eval(
        self, cases: Sequence[Case], exchanges: Sequence[Exchange] | None = None
    ) -> RecallEval:
        return RecallEval(
            within=3,
            exchanges=tuple(exchanges or (Exchange("a", A, "はい"), Exchange("d", D, "はい"))),
            cases=tuple(cases),
        )

    def test_IT_030_004_断る判断が測る側の独立した一段にある(self, tmp_path: Path) -> None:
        unconfirmed = self._eval([Case("c", C, ("a",), (), confirmed=False)])
        confirmed = self._eval([Case("c", C, ("a",), (), confirmed=True)])
        plain = self._eval([Case("c", C, ("a",), ())])
        unknown = self._eval([Case("c", C, ("zzz",), ())])
        same_case = self._eval([Case("c", C, ("a",), ()), Case("c", E, ("d",), ())])
        same_exchange = self._eval(
            [Case("c", C, ("a",), ())], (Exchange("a", A, "はい"), Exchange("a", D, "はい"))
        )

        with pytest.raises(CannotMeasure, match="確認していない件が 1 件"):
            self._measure(unconfirmed)
        with pytest.raises(CannotMeasure, match="無いやりとりを指している"):
            self._measure(unknown)
        with pytest.raises(CannotMeasure, match="件の名前が重なっている"):
            self._measure(same_case)
        with pytest.raises(CannotMeasure, match="やりとりの名前が重なっている"):
            self._measure(same_exchange)
        self._measure(confirmed)
        self._measure(plain)
        # 読み手は読むだけで判断しない。
        text = "\n".join(
            [
                "within = 3",
                "[[exchange]]",
                'name = "a"',
                'utterance = "x"',
                'reply = "y"',
                "[[case]]",
                'name = "c"',
                'utterance = "z"',
                'expected = ["a"]',
                "forbidden = []",
                "confirmed = false",
                "",
            ]
        )
        path = tmp_path / "e.toml"
        _ = path.write_text(text, encoding="utf-8")
        assert EvalFile(path).read().cases[0].confirmed is False


class TestIT030005:
    def _eval(self) -> RecallEval:
        return RecallEval(
            3,
            (Exchange("a", A, "いいですね"),),
            (Case("c", C, ("a",), (), False, (("a", 0.1),)),),
        )

    def test_IT_030_005_書き手が境界を守り途中の失敗では何も書かれない(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        existing = tmp_path / "existing.toml"
        _ = existing.write_text("x", encoding="utf-8")
        directory = tmp_path / "dir"
        directory.mkdir()
        fresh = tmp_path / "fresh.toml"

        for bad in (repo / "d.toml", existing, directory):
            with pytest.raises(CannotDraft):
                DraftFile().write(bad, self._eval())
        assert existing.read_text(encoding="utf-8") == "x"
        DraftFile().write(fresh, self._eval())
        loaded = EvalFile(fresh).read()
        assert loaded.exchanges[0] == Exchange("a", A, "いいですね")
        assert loaded.cases[0].utterance == C

        place = tmp_path / "records"
        _ = write(place, "a.jsonl", claude_code_lines("s1", WORKSPACE, TURNS))
        out = tmp_path / "never.toml"
        with pytest.raises(CannotDraft):
            _ = Drafting([ClaudeCodeRecords()], FailingJudge("途中で失敗"), DraftFile()).run(
                [place], out
            )
        assert not out.exists()


class TestIT030006:
    def test_IT_030_006_数は手順が返し伝え方と終了状態は入口が持つ(self, tmp_path: Path) -> None:
        place = tmp_path / "records"
        _ = write(place, "a.jsonl", claude_code_lines("s1", WORKSPACE, TURNS + FILLERS))
        _ = write(place, "c.jsonl", claude_code_lines("s9", WORKSPACE, LATER, first_minute=90))
        out = tmp_path / "d.toml"
        written, errors = io.StringIO(), io.StringIO()
        real, sys.stderr = sys.stderr, errors
        try:
            code = Drafter([place], out, judge=FixedJudge({C: [A]}), writing=written).run()
            missing = Drafter(
                [tmp_path / "nowhere"],
                tmp_path / "x.toml",
                judge=FixedJudge({}),
                writing=io.StringIO(),
            ).run()
            no_out = Entry(["yadori", "evals", "draft", "--from", str(place)]).run()
            unknown = Entry(
                [
                    "yadori",
                    "evals",
                    "draft",
                    "--from",
                    str(place),
                    "--out",
                    str(out),
                    "--bogus",
                    "1",
                ]
            ).run()
        finally:
            sys.stderr = real
        lines = written.getvalue().splitlines()
        assert code == 0 and len(lines) == 4
        assert "記録: 2 セッション、中身のある発話 12 件（読めず飛ばしたファイル 0）" in lines[0]
        assert "覚えさせる発話: 11 件" in lines[1] and "件: 1 件" in lines[2]
        assert missing == 1 and "下書きを作れません" in errors.getvalue()
        assert no_out == 1 and unknown == 1
        assert USAGE in errors.getvalue()
        assert "Codex の記録は判定のために別の相手へ渡る" in USAGE


@final
class _RecordingCall:
    def __init__(self, answer: str | Exception) -> None:
        self._answer: str | Exception = answer
        self.sent: list[tuple[str, str]] = []

    def ask(self, preface: str, spoken: str) -> str:
        self.sent.append((preface, spoken))
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


class TestIT030007:
    def test_IT_030_007_判定の実装は発話だけを送り返事の不備を下書きの失敗にする(self) -> None:
        utterances = [A, B, C]
        good = _RecordingCall('[{"later": 3, "earlier": [1, 2]}]')

        pairs = ClaudeCodeJudge(good).pairs(utterances)

        assert {(pair.later, pair.earlier) for pair in pairs} == {(2, 0), (2, 1)}
        preface, spoken = good.sent[0]
        for text in (A, B, C):
            assert text in spoken
        for absent in ("いいですね", "2026", WORKSPACE, "workspace", "cwd"):
            assert absent not in spoken and absent not in preface
        bad_answers: list[str | Exception] = [
            '[{"later": 3, "earlier": [9]}]',
            '[{"later": 1, "earlier": [3]}]',
            "組は無いと思います",
            RuntimeError("対話する道具が失敗した: usage limit reached"),
            RuntimeError("対話する道具が失敗した: prompt is too long"),
        ]
        for answer in bad_answers:
            with pytest.raises(CannotDraft):
                _ = ClaudeCodeJudge(_RecordingCall(answer)).pairs(utterances)
        with pytest.raises(CannotDraft, match="置き場を絞って"):
            _ = ClaudeCodeJudge(_RecordingCall(RuntimeError("prompt is too long"))).pairs(
                utterances
            )
