"""IT-037: 追記の設計判断を、部品の口で確かめる。"""

from __future__ import annotations

import io
import os
import shutil
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import final

import pytest

from tests.appending import (
    ALONE,
    BOOK,
    BOOK_AGAIN,
    FIRST,
    FIRST_JUDGE,
    HOW,
    MOVIE,
    MOVIE_AGAIN,
    Relabeled,
    drafted,
    first_draft,
    first_records,
    judge_of,
    names,
    outside_covered,
    utterances,
)
from tests.records import (
    OTHER_WORKSPACE,
    START,
    WORKSPACE,
    FailingJudge,
    FixedJudge,
    claude_code_lines,
    write,
)
from yadori.adapter.embedding import CharacterPairs, Multilingual
from yadori.adapter.embedding import multilingual as multilingual_module
from yadori.adapter.evaluation import (
    ClaudeCodeJudge,
    ClaudeCodeRecords,
    CodexRecords,
    DraftFile,
    EvalFile,
)
from yadori.adapter.store import InMemoryMemories
from yadori.domain.evaluation import (
    Added,
    CannotDraft,
    CannotMeasure,
    Case,
    Covered,
    DrawnWith,
    Exchange,
    RecallEval,
    Recorded,
)
from yadori.domain.memory import (
    EmbeddingsUnavailable,
    HowToRecall,
    Memories,
    Provenance,
    Vector,
)
from yadori.infrastructure import entry as entry_module
from yadori.infrastructure.draft import Drafter
from yadori.infrastructure.entry import USAGE, Entry
from yadori.usecase.evaluation import Drafting, Measuring
from yadori.usecase.evaluation.draft import DRAFTED

PAIRS = Provenance(ai_model=None, tool="character-pairs", tool_version="v1")
DRAWN = DrawnWith(provenance=PAIRS, how=HOW, judge="fixed-judge")


def _drafting(
    judge: FixedJudge | FailingJudge,
    embeddings: object = None,
    fresh: Callable[[], Memories] = InMemoryMemories,
    how: HowToRecall = HOW,
) -> Drafting:
    return Drafting(
        [ClaudeCodeRecords(), CodexRecords()],
        judge,
        DraftFile(),
        embeddings or CharacterPairs(),  # pyright: ignore[reportArgumentType]
        fresh,
        how,
    )


def _minute(minute: int) -> datetime:
    return START + timedelta(minutes=minute)


class TestIT037001:
    def test_IT_037_001_前回の範囲が初回から下書きに残り読み戻せ違いを自分で答える(
        self, tmp_path: Path
    ) -> None:
        place, out = first_draft(tmp_path)

        loaded, covered = DraftFile().read(out)

        assert covered == Covered(
            until=_minute(102),
            places=(str(place.resolve()),),
            skipped=(),
            sessions=2,
            last_exchange=6,
            last_case=2,
            drawn_with=DRAWN,
        )
        assert covered.until.tzinfo is not None and covered.drawn_with.how == HOW
        assert len(loaded.exchanges) == 6 and len(loaded.cases) == 2
        text = out.read_text(encoding="utf-8")
        assert "候補を引いた" not in text and "[covered]" in text and "ai_model" not in text
        assert covered.drawn_with.provenance.ai_model is None

    def test_IT_037_001_引き方の違いを文で答え版だけは注意になる(self) -> None:
        other_model = DrawnWith(
            Provenance("some-model", "character-pairs", "v1"), HOW, "fixed-judge"
        )
        other_tool = DrawnWith(Provenance(None, "other-tool", "v1"), HOW, "fixed-judge")
        other_floor = DrawnWith(PAIRS, HowToRecall(2, 10, 0.2), "fixed-judge")
        other_judge = DrawnWith(PAIRS, HOW, "other")
        other_version = DrawnWith(Provenance(None, "character-pairs", "v2"), HOW, "fixed-judge")

        assert DRAWN.differs_from(other_model) is not None
        assert "埋め込みの AIモデル" in (DRAWN.differs_from(other_model) or "")
        assert "埋め込みを動かす道具" in (DRAWN.differs_from(other_tool) or "")
        assert "思い出し方" in (DRAWN.differs_from(other_floor) or "")
        assert "判定の AIモデル" in (DRAWN.differs_from(other_judge) or "")
        assert DRAWN.differs_from(other_version) is None
        assert "版" in (DRAWN.tool_version_changed(other_version) or "")
        assert DRAWN.tool_version_changed(DRAWN) is None
        covered = Covered(_minute(0), ("/a", "/b"), (), 1, 1, 1, DRAWN)
        assert covered.places_differ_from(("/b", "/a")) is None
        assert covered.places_differ_from(("/a", "/b", "/c")) is not None

    def test_IT_037_001_前回の範囲を持たないものと欄の欠けは断られる(self, tmp_path: Path) -> None:
        hand = tmp_path / "hand.toml"
        _ = hand.write_text(
            'within = 3\n\n[[exchange]]\nname = "a"\nutterance = "x"\nreply = "y"\n',
            encoding="utf-8",
        )
        lacking = tmp_path / "lacking.toml"
        _ = lacking.write_text(
            "within = 3\n\n[covered]\nplaces = []\nskipped = []\nsessions = 1\n"
            + "last_exchange = 1\nlast_case = 0\n"
            + 'drawn_with = { tool = "t", tool_version = "v", recent = 2, limit = 10, '
            + 'floor = 0.15, judge = "j" }\n'
            + '\n[[exchange]]\nname = "a"\nutterance = "x"\nreply = "y"\n',
            encoding="utf-8",
        )

        with pytest.raises(CannotDraft, match=r"前回の範囲を持たない.*新しい版の道具"):
            _ = DraftFile().read(hand)
        with pytest.raises(CannotDraft, match="until"):
            _ = DraftFile().read(lacking)

    def test_IT_037_001_AIモデルの名前がある出自も往復する(self, tmp_path: Path) -> None:
        out = tmp_path / "with-model.toml"
        covered = Covered(
            _minute(0),
            ("/a",),
            ("/a/broken.jsonl",),
            1,
            1,
            0,
            DrawnWith(Provenance("mini", "fastembed", "0.8.0"), HOW, "opus"),
        )
        recall_eval = RecallEval(3, (Exchange("e001", "x", "y"),), ())

        DraftFile().write(out, recall_eval, covered)
        _, loaded = DraftFile().read(out)

        assert loaded == covered
        assert 'ai_model = "mini"' in out.read_text(encoding="utf-8")


@final
class _CountingRecords:
    def __init__(self) -> None:
        self._inner: ClaudeCodeRecords = ClaudeCodeRecords()
        self.claimed: int = 0

    def claims(self, path: Path) -> bool:
        self.claimed += 1
        return self._inner.claims(path)

    def read(self, path: Path) -> tuple[Recorded, ...]:
        return self._inner.read(path)


class _Unavailable:
    @property
    def provenance(self) -> Provenance:
        return PAIRS

    @property
    def name(self) -> str:
        return PAIRS.index_name

    def of(self, text: str) -> Vector:
        del text
        raise EmbeddingsUnavailable("入れてください")


class TestIT037002:
    def test_IT_037_002_新しい発話の境界と数え方(self, tmp_path: Path) -> None:
        place, out = first_draft(tmp_path)
        inside = "冬に温泉へ行く計画を立てた"
        same = "来月の同窓会の幹事を頼まれた"
        old = "図書館で借りた小説を三冊返しました"
        _ = write(
            place,
            "inside.jsonl",
            claude_code_lines("s6", WORKSPACE, [(inside, "x")], first_minute=60),
        )
        _ = write(
            place, "same.jsonl", claude_code_lines("s7", WORKSPACE, [(same, "x")], first_minute=102)
        )
        _ = write(
            place,
            "after.jsonl",
            claude_code_lines(
                "s8",
                WORKSPACE,
                [
                    (MOVIE_AGAIN, "x"),
                    (BOOK, "前回の覚えさせる発話と同じ文言"),
                    (
                        "ベランダのトマトの苗を植えた話をもう一度聞かせてください",
                        "前回の問と同じ文言",
                    ),
                    ("いいよ", "相槌"),
                    (ALONE, "x"),
                    (ALONE, "同じ文言が二つ"),
                ],
                first_minute=200,
            ),
        )
        # 前回飛ばしたファイルは、今回読めれば古い時刻でも新しい発話になる。
        _, before = DraftFile().read(out)
        skipped_path = place / "old.jsonl"
        _ = write(
            place, "old.jsonl", claude_code_lines("s5", WORKSPACE, [(old, "x")], first_minute=50)
        )
        DraftFile().append(
            out,
            Added((), ()),
            Covered(
                before.until,
                before.places,
                (str(skipped_path.resolve()),),
                before.sessions,
                before.last_exchange,
                before.last_case,
                before.drawn_with,
            ),
        )
        _ = write(
            place,
            "cut.jsonl",
            claude_code_lines("s9", WORKSPACE, [("週末に自転車で走った", "x")], first_minute=300)
            + "{cut\n",
        )
        judge = judge_of({MOVIE_AGAIN: [MOVIE]})

        appended = _drafting(judge).append([place], out)

        assert appended.incoming == 3  # MOVIE_AGAIN、ALONE、old
        assert appended.asked + appended.unasked == appended.incoming
        assert {asking.utterance for asking in judge.askings} <= {MOVIE_AGAIN, ALONE, old}
        assert appended.new_sessions == 2  # s8 と s5
        _, covered = DraftFile().read(out)
        # 切れたファイルは読めないので、最大の時刻とセッション数に入らない。
        assert covered.until == _minute(210) and covered.sessions == 6
        assert covered.skipped == (str((place / "cut.jsonl").resolve()),)
        assert inside not in utterances(out, "exchange") and same not in utterances(out, "exchange")

    def test_IT_037_002_引き方が違えば記録を読まずに断り途中の失敗では一字も変わらない(
        self, tmp_path: Path
    ) -> None:
        place, out = first_draft(tmp_path)
        _ = write(
            place,
            "more.jsonl",
            claude_code_lines("s3", WORKSPACE, [(MOVIE_AGAIN, "x")], first_minute=200),
        )
        before = out.read_text(encoding="utf-8")
        counting = _CountingRecords()
        drafting = Drafting(
            [counting],
            FixedJudge(FIRST_JUDGE),
            DraftFile(),
            Relabeled(Provenance("other", "character-pairs", "v1")),
            InMemoryMemories,
            HOW,
        )

        with pytest.raises(CannotDraft, match="新しいファイルへ作り直してください"):
            _ = drafting.append([place], out)
        assert counting.claimed == 0
        with pytest.raises(CannotDraft, match="上限"):
            _ = _drafting(FailingJudge("上限に当たった")).append([place], out)
        with pytest.raises(EmbeddingsUnavailable):
            _ = _drafting(FixedJudge(FIRST_JUDGE), _Unavailable()).append([place], out)
        assert out.read_text(encoding="utf-8") == before


class TestIT037003:
    def test_IT_037_003_記憶に入るものと問いになるものが分かれ居場所は記録から引く(
        self, tmp_path: Path
    ) -> None:
        place, out = first_draft(tmp_path)
        garden = "庭の雑草を抜いて肥料をまきました"
        garden_again = "庭の雑草はまた生えてきましたか"
        other1 = "会議の議事録をまとめました"
        other2 = "会議の議事録は共有しましたか"
        # GARDEN の記録を消し、BOOK の返事を下書きの側で変える。
        _ = write(
            place,
            "first.jsonl",
            claude_code_lines("s1", WORKSPACE, [turn for turn in FIRST if turn[0] != garden]),
        )
        _ = out.write_text(
            out.read_text(encoding="utf-8").replace(
                'reply = "何を借りましたか"', 'reply = "下書きの返事"'
            ),
            encoding="utf-8",
        )
        _ = write(
            place,
            "more.jsonl",
            claude_code_lines(
                "s3",
                WORKSPACE,
                [(MOVIE_AGAIN, "x"), (BOOK_AGAIN, "x"), (garden_again, "x")],
                first_minute=200,
            ),
        )
        _ = write(
            place,
            "other.jsonl",
            claude_code_lines(
                "s4",
                OTHER_WORKSPACE,
                [
                    (other1, "x"),
                    ("来週の出張の宿を手配しました", "x"),
                    ("請求書の締め日を確認しました", "x"),
                    (other2, "x"),
                ],
                first_minute=300,
            ),
        )
        judge = judge_of(
            {MOVIE_AGAIN: [MOVIE], BOOK_AGAIN: [BOOK], garden_again: [garden], other2: [other1]}
        )
        kept: list[Memories] = []

        def capturing() -> Memories:
            memories = InMemoryMemories()
            kept.append(memories)
            return memories

        appended = _drafting(judge, fresh=capturing).append([place], out)

        asked = {asking.utterance for asking in judge.askings}
        assert asked <= {MOVIE_AGAIN, BOOK_AGAIN, garden_again, other2}
        candidates = {c for asking in judge.askings for c in asking.candidates}
        assert MOVIE in candidates and BOOK in candidates and garden not in candidates
        assert "ベランダのトマトの苗を植えた話をもう一度聞かせてください" not in candidates
        other_askings = [asking for asking in judge.askings if asking.utterance == other2]
        assert other_askings and set(other_askings[0].candidates) == {other1}
        cases = utterances(out, "case")
        assert MOVIE_AGAIN in cases and BOOK_AGAIN in cases and other2 in cases
        assert garden_again in utterances(out, "exchange")
        remembered = {
            episode.utterance: episode.reply
            for memories in kept
            for episode in memories.recent(DRAFTED.id, 100)
        }
        assert remembered[BOOK] == "下書きの返事" and garden not in remembered
        assert appended.added_cases == 3

    def test_IT_037_003_前回飛ばしたファイル由来の古い発話は自分より前からしか候補を引かない(
        self, tmp_path: Path
    ) -> None:
        place = tmp_path / "records"
        out = tmp_path / "draft.toml"
        old = "図書館で借りた小説を三冊返しました"
        old_lines = claude_code_lines("s5", WORKSPACE, [(old, "x")], first_minute=50)
        _ = first_records(place)
        _ = write(place, "broken.jsonl", old_lines + "{broken\n")
        _ = _drafting(FixedJudge(FIRST_JUDGE)).run([place], out)
        _ = write(place, "broken.jsonl", old_lines)
        _ = write(
            place,
            "more.jsonl",
            claude_code_lines("s3", WORKSPACE, [(BOOK_AGAIN, "x")], first_minute=200),
        )
        judge = judge_of({})

        _ = _drafting(judge).append([place], out)

        old_askings = [asking for asking in judge.askings if asking.utterance == old]
        assert old_askings
        assert BOOK in old_askings[0].candidates and BOOK_AGAIN not in old_askings[0].candidates
        assert utterances(out, "exchange")[-2:] == [old, BOOK_AGAIN]


class TestIT037004:
    def _previous(self, tmp_path: Path) -> tuple[Path, Path, list[str]]:
        topics = [
            "朝顔の種",
            "町内会の回覧板",
            "自転車の空気入れ",
            "新しい炊飯器",
            "英会話の教室",
            "屋根の雨漏り",
            "子どもの運動会",
            "電気料金の明細",
            "冷蔵庫の整理",
            "週末の登山",
            "犬の予防接種",
            "確定申告の書類",
            "古い写真の整理",
            "友人の結婚式",
            "台所の蛇口",
            "図書館の返却期限",
            "駅前の新しい店",
            "スマートフォンの機種変更",
            "庭の柿の木",
            "年賀状の準備",
        ]
        spoken = [f"{topic}について相談したいことがあります" for topic in topics]
        place = tmp_path / "records"
        _ = write(
            place, "a.jsonl", claude_code_lines("s1", WORKSPACE, [(one, "はい") for one in spoken])
        )
        out = tmp_path / "draft.toml"
        exchanges = tuple(
            Exchange(f"e{n:03d}", one, "はい") for n, one in enumerate(spoken, start=1)
        )
        cases = (
            Case("c001", "朝顔の種はいつ蒔きますか", ("e001",), (), True, (("e001", 0.3),)),
            Case("c002", "回覧板は回しましたか", ("e002",), (), True, (("e002", 0.3),)),
            Case("c-renamed", "炊飯器は届きましたか", ("e004",), (), False, (("e004", 0.3),)),
        )
        DraftFile().write(
            out,
            RecallEval(3, exchanges, cases),
            Covered(
                _minute(38),
                (str(place.resolve()),),
                (),
                1,
                20,
                5,
                DrawnWith(PAIRS, self._how(), "fixed-judge"),
            ),
        )
        return place, out, spoken

    def _how(self) -> HowToRecall:
        return HowToRecall(recent_turns=6, found_limit=30, relevance_floor=0.15)

    def test_IT_037_004_合わせた並びで解け直近で外れた発話が残り番号が続く(
        self, tmp_path: Path
    ) -> None:
        place, out, spoken = self._previous(tmp_path)
        n1 = "英会話の教室について相談した件はどうなりましたか"
        n2 = "図書館の返却期限について相談した件はどうなりましたか"
        n3 = ALONE
        _ = write(
            place,
            "b.jsonl",
            # 時刻の順は新1・新3・新2。新2 の思い出す時点で e016 は直近（6 往復）の外にある。
            claude_code_lines("s2", WORKSPACE, [(n1, "x"), (n3, "x"), (n2, "x")], first_minute=200),
        )
        judge = FixedJudge({n1: [spoken[4]], n2: [spoken[15]]})

        appended = _drafting(judge, how=self._how()).append([place], out)

        n2_askings = [asking for asking in judge.askings if asking.utterance == n2]
        assert n2_askings and spoken[15] in n2_askings[0].candidates
        assert names(out, "exchange")[-2:] == ["e021", "e022"]
        assert utterances(out, "exchange")[-2:] == [n3, n2]
        assert names(out, "case") == ["c001", "c002", "c-renamed", "c006"]
        loaded = EvalFile(out).read()
        assert loaded.cases[-1].utterance == n1 and loaded.cases[-1].expected == ("e005",)
        assert appended.added_exchanges == 2 and appended.added_cases == 1
        _, covered = DraftFile().read(out)
        assert covered.last_exchange == 22 and covered.last_case == 6
        _ = out.write_text(
            out.read_text(encoding="utf-8").replace("confirmed = false", "confirmed = true"),
            encoding="utf-8",
        )
        measured = Measuring(EvalFile(out).read(), InMemoryMemories, CharacterPairs()).at(
            self._how()
        )
        assert measured.total == 4 and measured.unmeasurable == 0

    def test_IT_037_004_壊れた前回の分では何も書かれない(self, tmp_path: Path) -> None:
        place, out, _ = self._previous(tmp_path)
        _ = out.write_text(
            out.read_text(encoding="utf-8").replace('expected = ["e002"]', 'expected = ["e099"]'),
            encoding="utf-8",
        )
        _ = write(
            place, "b.jsonl", claude_code_lines("s2", WORKSPACE, [(ALONE, "x")], first_minute=200)
        )
        before = out.read_text(encoding="utf-8")

        with pytest.raises(CannotDraft, match="e099"):
            _ = _drafting(FixedJudge({}), how=self._how()).append([place], out)

        assert out.read_text(encoding="utf-8") == before


class TestIT037005:
    def _draft(self, path: Path) -> Covered:
        covered = Covered(_minute(10), ("/a",), (), 1, 2, 1, DRAWN)
        DraftFile().write(
            path,
            RecallEval(
                3,
                (Exchange("e001", "x", "y"), Exchange("e002", "p", "q")),
                (Case("c001", "z", ("e001",), (), False, (("e001", 0.2),)),),
            ),
            covered,
        )
        return covered

    def test_IT_037_005_前回の分が一字も変わらず日本語の名前も書ける(self, tmp_path: Path) -> None:
        out = tmp_path / "d.toml"
        _ = self._draft(out)
        text = out.read_text(encoding="utf-8")
        e001 = text[text.index('[[exchange]]\nname = "e001"') :]
        e001 = e001[: e001.index("\n\n") + 2]
        text = text.replace(e001, "")
        text = text.replace('name = "e002"', 'name = "ピアノの話"') + "\n# 注釈\n\n" + e001
        # 人が見出しを字下げしても、TOML としては正しく、前回の分は残る。
        text = text.replace("[[exchange]]\n", "  [[exchange]]\n", 1).replace(
            "[covered]\n", "[covered] # 触らない\n"
        )
        text = text.replace("confirmed = false", "confirmed = true")
        _ = out.write_text(text, encoding="utf-8")
        before = out.read_text(encoding="utf-8")
        after_covered = Covered(_minute(20), ("/a",), ("/a/x.jsonl",), 2, 3, 2, DRAWN)

        DraftFile().append(
            out,
            Added(
                (Exchange("e003", "n", "m"),),
                (Case("c002", "w", ("ピアノの話",), (), False, (("ピアノの話", 0.4),)),),
            ),
            after_covered,
        )

        after = out.read_text(encoding="utf-8")
        assert outside_covered(after).startswith(outside_covered(before))
        loaded, covered = DraftFile().read(out)
        assert covered == after_covered
        assert [one.name for one in loaded.exchanges] == ["ピアノの話", "e001", "e003"]
        assert [one.name for one in loaded.cases] == ["c001", "c002"]
        assert loaded.cases[0].confirmed is True and loaded.cases[1].expected == ("ピアノの話",)
        assert "# 注釈" in after and '"ピアノの話" = 0.4' in after
        assert "# 触らない" not in after  # 見出しの行の注釈は残らない（注記に書いてある）

    def test_IT_037_005_途中で落ちても元が残り問ゼロの前回を読め境界を守る(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = tmp_path / "d.toml"
        covered = self._draft(out)
        before = out.read_text(encoding="utf-8")

        def broken_replace(src: str, dst: str) -> None:
            del src, dst
            raise OSError("書けない")

        monkeypatch.setattr(os, "replace", broken_replace)
        with pytest.raises(OSError):
            DraftFile().append(out, Added((), ()), covered)
        monkeypatch.undo()
        assert out.read_text(encoding="utf-8") == before
        assert [one.name for one in tmp_path.iterdir()] == ["d.toml"]

        text = before.split("\n[[case]]\n")[0]
        _ = out.write_text(text, encoding="utf-8")
        loaded, _ = DraftFile().read(out)
        assert loaded.cases == ()
        DraftFile().append(out, Added((), ()), covered)
        with pytest.raises(CannotMeasure, match="問が一つもありません"):
            _ = Measuring(EvalFile(out).read(), InMemoryMemories, CharacterPairs()).at(HOW)

        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        inside = repo / "d.toml"
        _ = inside.write_text(before, encoding="utf-8")
        # 境界と見出しの探し方は読む段で確かめ、判定を呼ぶ前に断る。
        with pytest.raises(CannotDraft, match="リポジトリの配下"):
            _ = DraftFile().read(inside)
        inline = tmp_path / "inline.toml"
        _ = inline.write_text(
            "within = 3\ncovered = { until = 2026-01-01T09:00:00+00:00, places = [], skipped = [], "
            + 'sessions = 1, last_exchange = 1, last_case = 0, drawn_with = { tool = "t", '
            + 'tool_version = "v", recent = 2, limit = 10, floor = 0.15, judge = "j" } }\n'
            + '\n[[exchange]]\nname = "a"\nutterance = "x"\nreply = "y"\n',
            encoding="utf-8",
        )
        with pytest.raises(CannotDraft, match="前回の範囲を持たない"):
            _ = DraftFile().read(inline)
        hand = tmp_path / "hand.toml"
        _ = hand.write_text("within = 3\n", encoding="utf-8")
        for bad, reason in (
            (tmp_path / "none.toml", "がありません"),
            (tmp_path, "ディレクトリ"),
            (inside, "リポジトリの配下"),
            (hand, "前回の範囲を持たない"),
        ):
            with pytest.raises(CannotDraft, match=reason):
                DraftFile().append(bad, Added((), ()), covered)


class TestIT037006:
    def _entry(
        self, argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, judge: FixedJudge
    ) -> tuple[int, str, str]:
        written, errors = io.StringIO(), io.StringIO()

        def fixed(places: Sequence[Path], out: Path, append: bool = False) -> Drafter:
            return Drafter(
                places,
                out,
                append=append,
                judge=judge,
                embeddings=CharacterPairs(),
                how=HOW,
                writing=written,
            )

        monkeypatch.setattr(entry_module, "Drafter", fixed)
        real = sys.stderr
        sys.stderr = errors
        try:
            code = Entry(argv).run()
        finally:
            sys.stderr = real
        del tmp_path
        return code, written.getvalue(), errors.getvalue()

    def test_IT_037_006_値を取らない引数の読み方と追記の結果の伝え方(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        place, out = first_draft(tmp_path)
        _ = write(
            place,
            "more.jsonl",
            claude_code_lines("s3", WORKSPACE, [(MOVIE_AGAIN, "x")], first_minute=200),
        )
        judge = judge_of({MOVIE_AGAIN: [MOVIE]})
        outputs: list[str] = []
        for position in range(3):
            copy = tmp_path / "out" / f"copy{position}.toml"
            _ = shutil.copy(out, copy)
            argv = ["yadori", "evals", "draft", "--from", str(place), "--out", str(copy)]
            argv.insert(3 + position * 2, "--append")
            code, written, errors = self._entry(argv, tmp_path, monkeypatch, judge)
            assert code == 0, errors
            outputs.append(written.replace(str(copy), "OUT"))
            assert names(copy, "case") == ["c001", "c002", "c003"]
        assert len(set(outputs)) == 1
        assert (
            "前回の範囲: " in outputs[0]
            and "前回の下書き: 覚えさせる発話 6 件、問 2 問" in outputs[0]
        )
        assert "今回: 新しい記録 1 セッション、新しい発話 1 件" in outputs[0]
        assert "問: +1 問（すべて確認前）。前回の 2 問とその確認はそのまま" in outputs[0]
        assert "confirmed = true にしてください" in outputs[0]

        base = ["yadori", "evals", "draft", "--from", str(place), "--out", str(out)]
        code, written, errors = self._entry(
            [*base, "--append", "--append"], tmp_path, monkeypatch, judge
        )
        assert code == 1 and written == "" and USAGE in errors
        code, written, errors = self._entry(
            ["yadori", "measure", "--append"], tmp_path, monkeypatch, judge
        )
        assert code == 1 and USAGE in errors
        code, written, errors = self._entry(base, tmp_path, monkeypatch, judge)
        assert code == 1 and "既にあります" in errors
        code, written, errors = self._entry(
            [*base[:-1], str(tmp_path / "out" / "none.toml"), "--append"],
            tmp_path,
            monkeypatch,
            judge,
        )
        assert code == 1 and "がありません" in errors
        code, written, errors = self._entry([*base, "--append"], tmp_path, monkeypatch, judge)
        assert code == 0 and "問: +1 問" in written
        code, written, errors = self._entry([*base, "--append"], tmp_path, monkeypatch, judge)
        assert code == 0 and "問: 増えませんでした" in written
        assert "--append" in USAGE and "前回の範囲を持つ下書きにだけ" in USAGE


@final
class _Call:
    @property
    def model(self) -> str:
        return "recorded-model"

    def ask(self, preface: str, spoken: str) -> str:
        del preface, spoken
        return "[]"


def _fixed_version(name: str) -> str:
    del name
    return "0.8.0"


class TestIT037007:
    def test_IT_037_007_口が答える値の形(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 道具の版は導入されたものから読む。ここでは版だけを差し替え、AIモデルは読み込まない。
        monkeypatch.setattr(multilingual_module, "version", _fixed_version)
        meaning = Multilingual().provenance
        assert meaning == Provenance("paraphrase-multilingual-MiniLM-L12-v2", "fastembed", "0.8.0")
        assert Multilingual().name == meaning.index_name
        assert Multilingual().name == "paraphrase-multilingual-MiniLM-L12-v2/fastembed-0.8.0"
        assert (
            CharacterPairs().provenance == PAIRS and CharacterPairs().name == "character-pairs-v1"
        )
        assert ClaudeCodeJudge(_Call()).name == "recorded-model"

        place = first_records(tmp_path / "records")
        _ = write(place, "broken.jsonl", "{broken\n")
        draft = _drafting(FixedJudge(FIRST_JUDGE)).run([place], tmp_path / "d.toml")

        assert draft.skipped == (str((place / "broken.jsonl").resolve()),)
        code, written, _ = drafted(place, tmp_path / "e.toml")
        assert code == 0 and "読めず飛ばしたファイル 1" in written
