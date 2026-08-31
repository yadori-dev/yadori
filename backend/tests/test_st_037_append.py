"""ST-037: 既にある下書きへ、増えた記録の分だけを足す。

記録は架空の会話で作る。候補は文字の埋め込みと下書き用の条件で引き、判定は答えを
固定したものに差し替える。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.appending import (
    ALONE,
    BOOK,
    BOOK_AGAIN,
    FIRST,
    HOW,
    MOVIE,
    MOVIE_AGAIN,
    NOD,
    TAX,
    TAX_AGAIN,
    TOMATO,
    TOMATO_AGAIN,
    TOMATO_THIRD,
    Relabeled,
    covered_of,
    drafted,
    failing,
    first_draft,
    judge_of,
    names,
    outside_covered,
    read,
    utterances,
)
from tests.records import WORKSPACE, FixedJudge, claude_code_lines, write
from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.evaluation import EvalFile
from yadori.adapter.store import InMemoryMemories
from yadori.domain.memory import EmbeddingsUnavailable, HowToRecall, Provenance, Vector
from yadori.usecase.evaluation import Measuring

TAX_LIKE = "住民税の納付書は払い終わりましたか"
OLD = "図書館で借りた小説を三冊返しました"
INSIDE = "冬に温泉へ行く計画を立てた"
SAME = "来月の同窓会の幹事を頼まれた"
CUT = "週末に自転車で川沿いを走った"


def _confirmed_all(out: Path) -> None:
    _ = out.write_text(
        out.read_text(encoding="utf-8").replace("confirmed = false", "confirmed = true"),
        encoding="utf-8",
    )


def _remove_case(out: Path, name: str) -> None:
    """人が問を消す。その [[case]] の塊だけを取り除く。"""
    text = out.read_text(encoding="utf-8")
    blocks = text.split("\n[[case]]\n")
    kept = [blocks[0]] + [block for block in blocks[1:] if f'name = "{name}"' not in block]
    _ = out.write_text("\n[[case]]\n".join(kept), encoding="utf-8")


class TestST037001:
    def test_ST_037_001_前回の内容が保たれ新しい問が末尾に足され測れる(
        self, tmp_path: Path
    ) -> None:
        place, out = first_draft(tmp_path)
        # 最初の下書き: やりとり e001〜e006、問 c001（TOMATO_AGAIN）と c002（TAX_AGAIN）。
        assert names(out, "case") == ["c001", "c002"]
        _remove_case(out, "c002")
        text = out.read_text(encoding="utf-8")
        text = text.replace('name = "c001"', 'name = "c-tomato"')
        text = text.replace("confirmed = false", "confirmed = true")
        text = text.replace("forbidden = []", 'forbidden = ["e004"]')
        # やりとりの順を入れ替え、注釈を書く。
        e005 = text[text.index('[[exchange]]\nname = "e005"') :]
        e005 = e005[: e005.index("\n\n") + 2]
        text = text.replace(e005, "") + "# 人の注釈\n" + e005
        _ = out.write_text(text, encoding="utf-8")
        before = out.read_text(encoding="utf-8")
        _ = write(
            place,
            "more.jsonl",
            claude_code_lines(
                "s3",
                WORKSPACE,
                [
                    (ALONE, "はい"),
                    (BOOK_AGAIN, "読み終えました"),
                    (MOVIE_AGAIN, "題名は"),
                    (TOMATO, "同じ文言"),
                ],
                first_minute=200,
            ),
        )

        code, written, errors = drafted(
            place, out, judge_of({MOVIE_AGAIN: [MOVIE], BOOK_AGAIN: [BOOK]}), append=True
        )

        assert code == 0, errors
        after = out.read_text(encoding="utf-8")
        assert outside_covered(after).startswith(outside_covered(before))
        assert names(out, "exchange") == ["e001", "e002", "e003", "e004", "e006", "e005", "e007"]
        assert names(out, "case") == ["c-tomato", "c003", "c004"]
        assert utterances(out, "exchange").count(TOMATO) == 1
        loaded = EvalFile(out).read()
        by_name = {case.name: case for case in loaded.cases}
        assert by_name["c-tomato"].confirmed is True and by_name["c-tomato"].forbidden == ("e004",)
        assert by_name["c003"].confirmed is False and by_name["c004"].confirmed is False
        assert by_name["c003"].expected == ("e004",) and by_name["c004"].expected == ("e003",)
        assert covered_of(out)["last_exchange"] == 7 and covered_of(out)["last_case"] == 4
        assert "問: +2 問（すべて確認前）。前回の 1 問とその確認はそのまま" in written
        _confirmed_all(out)
        measured = Measuring(EvalFile(out).read(), InMemoryMemories, CharacterPairs()).at(HOW)
        assert measured.total == 3

    def test_ST_037_001_合わせた並びの末尾の直近に期待がある組は問にならず発話は残る(
        self, tmp_path: Path
    ) -> None:
        place, out = first_draft(tmp_path)
        # 新しい発話は一つだけ。合わせた並びの末尾二つは e006（PIANO）とその発話になり、
        # PIANO を指す組は直近として外れる。
        piano_again = "ピアノの発表会の曲は決まりましたか"
        _ = write(
            place,
            "more.jsonl",
            claude_code_lines("s3", WORKSPACE, [(piano_again, "はい")], first_minute=200),
        )

        code, written, errors = drafted(
            place, out, judge_of({piano_again: ["ピアノの発表会の曲を決めました"]}), append=True
        )

        assert code == 0, errors
        assert names(out, "case") == ["c001", "c002"]
        assert piano_again in utterances(out, "exchange")
        assert "問: 増えませんでした" in written


class TestST037002:
    def test_ST_037_002_判定へ渡るのは新しい発話だけ(self, tmp_path: Path) -> None:
        place, out = first_draft(tmp_path)
        _remove_case(out, "c002")  # TAX_AGAIN の問を人が消す
        _ = write(
            place,
            "more.jsonl",
            claude_code_lines(
                "s3",
                WORKSPACE,
                [
                    (MOVIE_AGAIN, "題名は"),
                    (TOMATO_THIRD, "育っています"),
                    (TAX_LIKE, "払いました"),
                    (ALONE, "はい"),
                    (TOMATO_AGAIN, "前と同じ文言"),
                    (NOD, "はい"),
                ],
                first_minute=200,
            ),
        )
        judge = FixedJudge(
            {MOVIE_AGAIN: [MOVIE], TOMATO_THIRD: [TOMATO_AGAIN, TOMATO], TAX_LIKE: [TAX_AGAIN]}
        )

        code, written, errors = drafted(place, out, judge, append=True)

        assert code == 0, errors
        asked = {asking.utterance for asking in judge.askings}
        assert asked == {MOVIE_AGAIN, TOMATO_THIRD, TAX_LIKE}
        candidates = {c for asking in judge.askings for c in asking.candidates}
        assert TOMATO_AGAIN not in candidates and TAX_AGAIN not in candidates
        assert TOMATO in candidates and TAX in candidates
        cases = utterances(out, "case")
        assert MOVIE_AGAIN in cases and TOMATO_THIRD in cases and TAX_LIKE not in cases
        assert "新しい発話 4 件" in written
        assert "判定へ渡した発話 3 件、候補が無く渡さなかった発話 1 件" in written


class _Unavailable:
    """出自は同じだが使えない埋め込み。"""

    @property
    def provenance(self) -> Provenance:
        return CharacterPairs().provenance

    @property
    def name(self) -> str:
        return self.provenance.index_name

    def of(self, text: str) -> Vector:
        del text
        raise EmbeddingsUnavailable(
            "意味を見る埋め込みを使えません。`uv sync` で依存を導入してください。"
        )


class TestST037003:
    def test_ST_037_003_対象でないものには足さない(self, tmp_path: Path) -> None:
        place, out = first_draft(tmp_path)
        hand = tmp_path / "out" / "hand.toml"
        _ = hand.write_text(
            'within = 3\n\n[[exchange]]\nname = "a"\nutterance = "x"\nreply = "y"\n\n'
            + '[[case]]\nname = "c"\nutterance = "z"\nexpected = ["a"]\n',
            encoding="utf-8",
        )
        old = tmp_path / "out" / "old.toml"
        _ = old.write_text(
            "# 実際の会話の記録から作った評価セットの下書き。\n" + hand.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        other = tmp_path / "other"
        other.mkdir()
        _ = write(
            place,
            "more.jsonl",
            claude_code_lines("s3", WORKSPACE, [(MOVIE_AGAIN, "x")], first_minute=200),
        )
        table: list[tuple[str, Path, dict[str, object], str]] = [
            ("手書き", hand, {}, "前回の範囲を持たない"),
            ("無いファイル", tmp_path / "out" / "none.toml", {}, "がありません"),
            ("前回の範囲を持たない下書き", old, {}, "前回の範囲を持たない"),
            (
                "AIモデル違い",
                out,
                {"embeddings": Relabeled(Provenance("some-model", "character-pairs", "v1"))},
                "埋め込みの AIモデル",
            ),
            ("下限違い", out, {"how": HowToRecall(2, 10, 0.2)}, "思い出し方"),
            ("判定違い", out, {"judge": FixedJudge({}, name="other-judge")}, "判定の AIモデル"),
            ("ディレクトリ追加", out, {"places": [place, other]}, "記録のディレクトリ"),
            (
                "道具違い",
                out,
                {"embeddings": Relabeled(Provenance(None, "other-tool", "v1"))},
                "埋め込みを動かす道具",
            ),
        ]
        for label, target, given, reason in table:
            before = target.read_text(encoding="utf-8") if target.exists() else None
            code, written, errors = drafted(place, target, append=True, **given)  # pyright: ignore[reportArgumentType]
            assert code == 1 and written == "", label
            assert reason in errors, (label, errors)
            if target.exists():
                assert target.read_text(encoding="utf-8") == before, label
            if before is not None and label not in ("手書き", "前回の範囲を持たない下書き"):
                assert "新しいファイルへ作り直してください" in errors, label
            elif before is not None:
                assert "新しい版の道具で作り直してください" in errors, label

    def test_ST_037_003_道具の版だけが違えば足され注意が出る(self, tmp_path: Path) -> None:
        place, out = first_draft(tmp_path)
        _ = write(
            place,
            "more.jsonl",
            claude_code_lines("s3", WORKSPACE, [(MOVIE_AGAIN, "x")], first_minute=200),
        )

        code, written, errors = drafted(
            place,
            out,
            judge_of({MOVIE_AGAIN: [MOVIE]}),
            append=True,
            embeddings=Relabeled(Provenance(None, "character-pairs", "v2")),
        )

        assert code == 0, errors
        assert "注意: 埋め込みを動かす道具の版が前回（character-pairs-v1）と違います" in written
        assert MOVIE_AGAIN in utterances(out, "case")
        drawn = covered_of(out)["drawn_with"]
        assert isinstance(drawn, dict) and drawn["tool_version"] == "v2"  # pyright: ignore[reportUnknownMemberType]


class TestST037004:
    def test_ST_037_004_途中の失敗で下書きが変わらず増えていなければ範囲だけ進む(
        self, tmp_path: Path
    ) -> None:
        place, out = first_draft(tmp_path)
        _ = write(
            place,
            "more.jsonl",
            claude_code_lines("s3", WORKSPACE, [(MOVIE_AGAIN, "x")], first_minute=200),
        )
        before = out.read_text(encoding="utf-8")

        for label, given, reason in (
            ("判定が失敗", {"judge": failing("上限に当たった")}, "上限に当たった"),
            ("埋め込みが使えない", {"embeddings": _Unavailable()}, "uv sync"),
        ):
            code, written, errors = drafted(place, out, append=True, **given)  # pyright: ignore[reportArgumentType]
            assert code == 1 and written == "" and reason in errors, (label, errors)
            assert out.read_text(encoding="utf-8") == before, label

        kept = tmp_path / "kept"
        _ = shutil.move(place, kept)
        code, written, errors = drafted(place, out, append=True)
        assert code == 1 and "記録のディレクトリ" in errors and "がありません" in errors
        assert out.read_text(encoding="utf-8") == before
        _ = shutil.move(kept, place)
        (place / "more.jsonl").unlink()

        code, written, errors = drafted(place, out, append=True)

        assert code == 0, errors
        assert (
            "問: 増えませんでした。前回の範囲だけを進めました。前回の 2 問とその確認はそのまま"
            in written
        )
        assert names(out, "case") == ["c001", "c002"]
        assert outside_covered(out.read_text(encoding="utf-8")) == outside_covered(before)
        assert covered_of(out)["sessions"] == 2


class TestST037005:
    def test_ST_037_005_範囲の境界どおりに読み画面で何が起きたかが読める(
        self, tmp_path: Path
    ) -> None:
        place = tmp_path / "records"
        out = tmp_path / "out" / "draft.toml"
        out.parent.mkdir()
        _ = write(place, "first.jsonl", claude_code_lines("s1", WORKSPACE, FIRST))
        _ = write(
            place,
            "later.jsonl",
            claude_code_lines(
                "s2", WORKSPACE, [(TOMATO_AGAIN, "はい"), (TAX_AGAIN, "はい")], first_minute=100
            ),
        )
        old_lines = claude_code_lines("s5", WORKSPACE, [(OLD, "返しました")], first_minute=50)
        _ = write(place, "broken.jsonl", old_lines + "{broken\n")
        code, _, errors = drafted(place, out)
        assert code == 0, errors
        covered = covered_of(out)
        assert covered["skipped"] == [str((place / "broken.jsonl").resolve())]
        assert covered["sessions"] == 2
        # 範囲の中、同じ時刻、後、直したファイル、今回切れたファイル。
        _ = write(
            place,
            "inside.jsonl",
            claude_code_lines("s6", WORKSPACE, [(INSIDE, "はい")], first_minute=60),
        )
        _ = write(
            place,
            "same.jsonl",
            claude_code_lines("s7", WORKSPACE, [(SAME, "はい")], first_minute=102),
        )
        _ = write(
            place,
            "after.jsonl",
            claude_code_lines("s8", WORKSPACE, [(MOVIE_AGAIN, "はい")], first_minute=200),
        )
        _ = write(place, "broken.jsonl", old_lines)
        cut_lines = claude_code_lines("s9", WORKSPACE, [(CUT, "はい")], first_minute=300)
        _ = write(place, "cut.jsonl", cut_lines + "{cut\n")
        judge = judge_of({MOVIE_AGAIN: [MOVIE]})

        code, written, errors = drafted(place, out, judge, append=True)

        assert code == 0, errors
        exchanges = utterances(out, "exchange")
        assert INSIDE not in exchanges and SAME not in exchanges
        assert exchanges[-1] == OLD
        assert utterances(out, "case")[-1] == MOVIE_AGAIN
        first_utterances = {spoken for spoken, _ in FIRST}
        old_askings = [asking for asking in judge.askings if asking.utterance == OLD]
        assert old_askings and set(old_askings[0].candidates) <= first_utterances
        assert covered_of(out)["skipped"] == [str((place / "cut.jsonl").resolve())]
        assert "前回の範囲: " in written and "飛ばしたファイル 1、2 セッション" in written
        assert (
            "今回: 新しい記録 2 セッション、新しい発話 2 件（読めず飛ばしたファイル 1）" in written
        )
        assert "判定へ渡した発話" in written and "覚えさせる発話: +1 件" in written
        assert "問: +1 問（すべて確認前）。前回の 2 問とその確認はそのまま" in written

        _ = write(place, "cut.jsonl", cut_lines)
        code, written, errors = drafted(place, out, judge, append=True)

        assert code == 0, errors
        assert utterances(out, "exchange")[-1] == CUT
        assert covered_of(out)["skipped"] == []


@pytest.mark.parametrize("flag", ["--append"])
def test_ST_037_入口の例の形で使える(flag: str, tmp_path: Path) -> None:
    """入口の例に書いた行が、実際の出力に現れる。"""
    place, out = first_draft(tmp_path)
    _ = write(
        place,
        "more.jsonl",
        claude_code_lines("s3", WORKSPACE, [(MOVIE_AGAIN, "x")], first_minute=200),
    )
    del flag

    code, written, errors = drafted(place, out, judge_of({MOVIE_AGAIN: [MOVIE]}), append=True)

    assert code == 0, errors
    lines = written.splitlines()
    assert lines[0].startswith("候補を引いた 埋め込み: AIモデル無し（character-pairs-v1） / 条件: ")
    assert lines[1].startswith("前回の範囲: ") and lines[2].startswith(
        "前回の下書き: 覚えさせる発話 6 件、問 2 問"
    )
    assert lines[3].startswith("今回: 新しい記録 1 セッション、新しい発話 1 件")
    assert read(out)["within"] == 3
