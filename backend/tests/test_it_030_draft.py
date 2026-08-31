"""INC-030 の結合テスト。設計の境界と、その構造を選んだ理由を確かめる。"""

from __future__ import annotations

import io
import sys
import tomllib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import ClassVar, final

import pytest

from tests.records import (
    OTHER_WORKSPACE,
    TEST_HOW,
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
from yadori.adapter.tool import ToolCallFailed, ToolLimitReached, TooLongForTool
from yadori.domain.evaluation import (
    Asking,
    CannotDraft,
    CannotMeasure,
    Case,
    Exchange,
    RecallEval,
    Recorded,
)
from yadori.domain.memory import EmbeddingsUnavailable, HowToRecall, Vector
from yadori.infrastructure.draft import Drafter
from yadori.infrastructure.entry import USAGE, Entry
from yadori.usecase.evaluation import Drafting, Measuring

A = "ベランダにトマトの苗を植えました"
B = "水やりは朝と夕方どちらがいいですか"
D = "住民税の納付書が届きました"
E = "図書館で小説を三冊借りました"
NOD = "いいよ"
A2 = "トマトの苗はその後どうなりましたか"  # A を指す（文字を共有）
B2 = "水やりは朝がいいと聞きましたが本当ですか"  # B を指す
B3 = "朝の水やりの話の続きですが肥料も要りますか"  # B2 を指す（連鎖）
HOW = HowToRecall(recent_turns=1, found_limit=5, relevance_floor=0.21)

TURNS = [
    (A, "いいですね"),
    (NOD, "はい"),
    (B, "朝です"),
    (D, "期限をお忘れなく"),
    (E, "楽しみですね"),
]
# 別のセッションで前の話題を指す発話。直近の外に置く。
LATER = [(A2, "トマトの件ですね"), (B2, "本当です")]
FILLERS = [(f"別の話題その{n}について相談です", "はい") for n in range(7)]


def _same(one: Recorded) -> tuple[str, str, datetime, str]:
    return (one.utterance, one.reply, one.at, one.workspace)


def _drafting(judge: FixedJudge | FailingJudge, embeddings: object = None) -> Drafting:
    return Drafting(
        [ClaudeCodeRecords(), CodexRecords()],
        judge,
        DraftFile(),
        embeddings or CharacterPairs(),  # pyright: ignore[reportArgumentType]
        InMemoryMemories,
        TEST_HOW,
    )


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
        _ = write(
            place, "broken.jsonl", claude_code_lines("s3", WORKSPACE, TURNS[:1]) + "{broken\n"
        )
        _ = write(place, "unknown.jsonl", '{"hello": "world"}\n')
        _ = write(place, "later.jsonl", claude_code_lines("s9", WORKSPACE, LATER, first_minute=90))
        out = tmp_path / "draft.toml"

        from_claude = ClaudeCodeRecords().read(place / "claude.jsonl")
        from_codex = CodexRecords().read(place / "codex.jsonl")
        draft = _drafting(FixedJudge({A2: [A]})).run([place], out)

        assert [_same(one) for one in from_claude] == [_same(one) for one in from_codex]
        assert all(one.session for one in from_claude + from_codex)
        assert not any("<" in one.utterance for one in from_claude)
        # 短い相槌は読み手からは返り、中身が無いと自分で答える。除くのは手順の側。
        nods = [one for one in from_claude if one.utterance == NOD]
        assert nods and not nods[0].has_substance()
        assert draft.skipped_files == 2


@final
class _FirstCharacter:
    """先頭の文字だけを見る別の埋め込み。同じ文でも候補が変わる。"""

    @property
    def name(self) -> str:
        return "first-character-v1"

    def of(self, text: str) -> Vector:
        code = ord(text[0]) if text else 0
        return tuple(1.0 if place == code % 64 else 0.0 for place in range(64))


@final
class _Unavailable:
    @property
    def name(self) -> str:
        return "unavailable"

    def of(self, text: str) -> Vector:
        del text
        raise EmbeddingsUnavailable("入れてください")


class TestIT030002:
    def _place(self, tmp_path: Path) -> Path:
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
        return place

    def test_IT_030_002_候補は同じ作業場所の前の発話だけで判定には発話と候補だけが渡る(
        self, tmp_path: Path
    ) -> None:
        judge = FixedJudge({A2: [A]})

        _ = _drafting(judge).run([self._place(tmp_path)], tmp_path / "d.toml")

        asked = {asking.utterance: asking.candidates for asking in judge.askings}
        assert set(asked) <= {A2, B2, "議事録の宛先は誰ですか"}
        garden = {A, B, D, E, A2, B2}
        for utterance, candidates in asked.items():
            assert len(candidates) <= TEST_HOW.found_limit
            if utterance in garden:
                assert set(candidates) <= garden  # 別の作業場所の発話は候補にならない
            else:
                assert not set(candidates) & garden
        # 直近往復数以内（直前）の発話は候補に入らない。
        assert "会議の議事録をまとめてください" not in asked.get("議事録の宛先は誰ですか", ())

    def test_IT_030_002_埋め込みを差し替えると候補が変わる(self, tmp_path: Path) -> None:
        with_characters = FixedJudge({A2: [A]})
        with_reversed = FixedJudge({A2: [A]})

        _ = _drafting(with_characters).run([self._place(tmp_path)], tmp_path / "d1.toml")
        with pytest.raises(CannotDraft):
            _ = _drafting(with_reversed, _FirstCharacter()).run(
                [self._place(tmp_path)], tmp_path / "d2.toml"
            )

        assert any(A in asking.candidates for asking in with_characters.askings)
        assert not any(A in asking.candidates for asking in with_reversed.askings)

    def test_IT_030_002_判定と埋め込みの失敗は握られずに届き下書きは書かれない(
        self, tmp_path: Path
    ) -> None:
        place = self._place(tmp_path)
        out = tmp_path / "d.toml"

        with pytest.raises(CannotDraft, match="上限"):
            _ = _drafting(FailingJudge("上限に当たった")).run([place], out)
        with pytest.raises(EmbeddingsUnavailable):
            _ = _drafting(FixedJudge({A2: [A]}), _Unavailable()).run([place], out)

        assert not out.exists()


class TestIT030003:
    def test_IT_030_003_判定の結果をそのまま使わず測れる形へ解いてから書く(
        self, tmp_path: Path
    ) -> None:
        place = tmp_path / "records"
        _ = write(place, "a.jsonl", claude_code_lines("s1", WORKSPACE, TURNS))
        _ = write(place, "dup.jsonl", codex_lines("s2", WORKSPACE, [(A, "重複")], first_minute=100))
        _ = write(place, "c.jsonl", claude_code_lines("s9", WORKSPACE, LATER, first_minute=110))
        _ = write(
            place,
            "late.jsonl",
            codex_lines("s3", WORKSPACE, [*FILLERS[:3], (B3, "少し")], first_minute=200),
        )
        _ = write(place, "tail.jsonl", codex_lines("s4", WORKSPACE, FILLERS[3:], first_minute=300))
        out = tmp_path / "d.toml"
        # A2 は A と B を指す（複数）。B2 は B を指し、B3 は B2 を指す（連鎖）。
        judge = FixedJudge({A2: [A, B], B2: [B], B3: [B2]})

        _ = _drafting(judge).run([place], out)

        loaded = EvalFile(out).read()
        utterances = [one.utterance for one in loaded.exchanges]
        assert utterances.count(A) == 1
        cases = {case.utterance: case for case in loaded.cases}
        assert A2 in cases and B3 in cases
        assert B2 not in cases and B2 in utterances  # 連鎖の真ん中は期待に残る
        assert len({one.name for one in loaded.exchanges}) == len(loaded.exchanges)
        # 語の重なりの度合いは人が読む欄で、測る側の読み手は読まない。ファイルの中で確かめる。
        written = rows_of(tomllib.loads(out.read_text(encoding="utf-8")), "case")
        by_name = {str(row["name"]): row for row in written}
        assert set(names_of(by_name[cases[A2].name], "overlap")) == set(cases[A2].expected)
        confirmed = RecallEval(
            within=loaded.within,
            exchanges=loaded.exchanges,
            cases=tuple(
                Case(one.name, one.utterance, one.expected, one.forbidden, True, one.overlap)
                for one in loaded.cases
            ),
        )
        measured = Measuring(confirmed, InMemoryMemories, CharacterPairs()).at(HOW)
        assert measured.total == len(loaded.cases)


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
        unconfirmed = self._eval([Case("c", A2, ("a",), (), confirmed=False)])
        confirmed = self._eval([Case("c", A2, ("a",), (), confirmed=True)])
        plain = self._eval([Case("c", A2, ("a",), ())])
        unknown = self._eval([Case("c", A2, ("zzz",), ())])
        same_case = self._eval([Case("c", A2, ("a",), ()), Case("c", E, ("d",), ())])
        same_exchange = self._eval(
            [Case("c", A2, ("a",), ())], (Exchange("a", A, "はい"), Exchange("a", D, "はい"))
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
            (Case("c", A2, ("a",), (), False, (("a", 0.1),)),),
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
                DraftFile().write(bad, self._eval(), "埋め込み: x / 条件: y")
        assert existing.read_text(encoding="utf-8") == "x"
        DraftFile().write(fresh, self._eval(), "埋め込み: x / 条件: y")
        loaded = EvalFile(fresh).read()
        assert loaded.exchanges[0] == Exchange("a", A, "いいですね")
        assert loaded.cases[0].utterance == A2
        assert "# 候補を引いた 埋め込み: x / 条件: y" in fresh.read_text(encoding="utf-8")

        place = tmp_path / "records"
        _ = write(place, "a.jsonl", claude_code_lines("s1", WORKSPACE, TURNS))
        _ = write(place, "c.jsonl", claude_code_lines("s9", WORKSPACE, LATER, first_minute=90))
        out = tmp_path / "never.toml"
        with pytest.raises(CannotDraft):
            _ = _drafting(FailingJudge("途中で失敗")).run([place], out)
        assert not out.exists()


class TestIT030006:
    def test_IT_030_006_数は手順が返し伝え方と終了状態は入口が持つ(self, tmp_path: Path) -> None:
        place = tmp_path / "records"
        _ = write(place, "a.jsonl", claude_code_lines("s1", WORKSPACE, TURNS))
        _ = write(
            place, "c.jsonl", claude_code_lines("s9", WORKSPACE, LATER + FILLERS, first_minute=90)
        )
        out = tmp_path / "d.toml"
        written, errors = io.StringIO(), io.StringIO()
        real, sys.stderr = sys.stderr, errors
        try:
            code = Drafter(
                [place],
                out,
                judge=FixedJudge({A2: [A]}),
                embeddings=CharacterPairs(),
                how=TEST_HOW,
                writing=written,
            ).run()
            missing = Drafter(
                [tmp_path / "nowhere"],
                tmp_path / "x.toml",
                judge=FixedJudge({}),
                embeddings=CharacterPairs(),
                writing=io.StringIO(),
            ).run()
            zero = Drafter(
                [place],
                tmp_path / "zero.toml",
                judge=FixedJudge({}),
                embeddings=CharacterPairs(),
                how=TEST_HOW,
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
        assert code == 0 and len(lines) == 6
        assert lines[0].startswith("候補を引いた 埋め込み: character-pairs-v1 / 条件: ")
        assert "記録: 2 セッション、中身のある発話 13 件（読めず飛ばしたファイル 0）" in lines[1]
        assert "覚えさせる発話: 12 件" in lines[2] and "件: 1 件" in lines[3]
        assert "手で足せます" in lines[5]
        assert missing == 1 and "下書きを作れません" in errors.getvalue()
        assert zero == 1 and "件が一つも出ませんでした" in errors.getvalue()
        assert not (tmp_path / "zero.toml").exists()
        assert no_out == 1 and unknown == 1
        assert USAGE in errors.getvalue()
        assert "別の相手へ渡ることになる" in USAGE


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
    ASKINGS: ClassVar[list[Asking]] = [Asking(A2, (A, D)), Asking(B2, (B,))]

    def test_IT_030_007_判定の実装は発話と候補だけを送り返事の不備を下書きの失敗にする(
        self,
    ) -> None:
        good = _RecordingCall('[{"q": 1, "same": [1]}, {"q": 2, "same": []}]')

        pairs = ClaudeCodeJudge(good).pairs(self.ASKINGS)

        assert {(pair.later, pair.earlier) for pair in pairs} == {(0, 0)}
        preface, spoken = good.sent[0]
        for text in (A2, A, D, B2, B):
            assert text in spoken
        assert E not in spoken  # 候補に無い発話は送られない
        for absent in ("いいですね", "2026", WORKSPACE, "workspace", "cwd"):
            assert absent not in spoken and absent not in preface
        bad_answers: list[str | Exception] = [
            '[{"q": 1, "same": [3]}]',
            '[{"q": 9, "same": [1]}]',
            "組は無いと思います",
            ToolCallFailed("対話する道具を呼べなかった"),
            ToolLimitReached("利用の上限に当たった"),
            TooLongForTool("受け付けない大きさだった"),
        ]
        for answer in bad_answers:
            with pytest.raises(CannotDraft):
                _ = ClaudeCodeJudge(_RecordingCall(answer)).pairs(self.ASKINGS)
        # こちら側の不具合は言い換えずに伝わる。
        with pytest.raises(RuntimeError):
            _ = ClaudeCodeJudge(_RecordingCall(RuntimeError("こわれた"))).pairs(self.ASKINGS)
