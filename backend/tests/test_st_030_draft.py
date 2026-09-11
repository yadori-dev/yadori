"""INC-030 のシステムテスト。外の入口（下書きを作る、測る）から確かめる。

記録は架空の会話で作る。候補は決まった値を返す文字の埋め込みと下書き用の条件で引き、
判定は答えを固定したものに差し替える。候補に上がる発話は前の発話と文字を共有させ、
上がらない発話は共有させない。
"""

from __future__ import annotations

import io
import sys
import tomllib
from pathlib import Path

import pytest

from tests.records import (
    OTHER_WORKSPACE,
    TEST_HOW,
    WORKSPACE,
    FailingJudge,
    FixedJudge,
    claude_code_lines,
    claude_code_noise,
    claude_code_tool_turn,
    codex_lines,
    codex_noise,
    names_of,
    rows_of,
    text_of,
    write,
)
from tests.sora import Steady, fixed
from yadori.adapter.embedding import CharacterPairs, Weighing
from yadori.domain.evaluation import Judge
from yadori.domain.memory import EmbeddingsUnavailable, HowToRecall, Provenance, Vector
from yadori.infrastructure.draft import Drafter
from yadori.infrastructure.entry import USAGE
from yadori.infrastructure.measure import Measure

TOMATO = "ベランダにトマトの苗を植えました"
TAX = "住民税の納付書が届きました"
TAX_SOON = "住民税の納付書の期限はいつまででしたか"  # TAX の直後。直近が渡す
WATERING = "水やりは朝と夕方どちらがいいですか"
BOOKS = "図書館で小説を三冊借りました"
MOVIE = "昨日は古い映画を観ました"
TOMATO_LATER = "トマトの苗はその後どうなりましたか"  # TOMATO を指す
PARCEL = "納付書ではなく届いた荷物の話です"  # TAX が候補に上がるが話題が違う
FOREX = "為替の見通しはどうでしょうか"  # 候補が一つも上がらない
WATERING_LATER = "水やりは朝がいいと聞きましたが本当ですか"  # WATERING を指す（連鎖の真ん中）
FERTILIZER = "朝の水やりの話の続きですが肥料も要りますか"  # WATERING_LATER を指す
NOD = "いいよ"
POINTING = "それ、どうなった？"
POINTING_MATTER = "この件で相談したい"  # 「〜の件」も指す語だけの発話
PASTED = "エラーの記録を貼ります。\n" + "\n".join(f"行 {n}: 処理に失敗しました" for n in range(60))
FILLERS = [
    "新しい鍵盤楽器が届きました",
    "洗濯物がよく乾きました",
    "歯医者の予約を来週に取りました",
    "近所で工事が始まるそうです",
    "豆を挽いて珈琲を淹れました",
    "自転車のタイヤに空気を入れました",
    "窓を拭いたら部屋が明るくなりました",
]
SAFE = {TOMATO_LATER: [TOMATO]}


def _place(tmp_path: Path) -> Path:
    """二つの形式の記録と雑音を置いたディレクトリ。"""
    place = tmp_path / "records"
    turns = [
        (TOMATO, "いいですね"),
        (NOD, "はい"),
        (TAX, "期限をお忘れなく"),
        (TAX_SOON, "来月末です"),
        (POINTING, "はい"),
        (POINTING_MATTER, "はい"),
        (PASTED, "見ました"),
    ]
    _ = write(place / "claude", "a.jsonl", claude_code_lines("s1", WORKSPACE, turns))
    _ = write(place / "claude", "noise.jsonl", claude_code_noise("s2", WORKSPACE, minute=30))
    codex_turns = [(WATERING, "朝がおすすめです"), (BOOKS, "楽しみですね"), (TOMATO, "重複です")]
    _ = write(
        place / "codex", "b.jsonl", codex_lines("s3", WORKSPACE, codex_turns, first_minute=40)
    )
    _ = write(place / "codex", "c.jsonl", codex_noise("s3", WORKSPACE, minute=50))
    later = (
        [
            (TOMATO_LATER, "トマトの件ですね"),
            (PARCEL, "荷物ですね"),
            (FOREX, "分かりません"),
            (WATERING_LATER, "本当です"),
            (FILLERS[0], "はい"),
            (FILLERS[1], "はい"),
            (FILLERS[2], "はい"),
            (FERTILIZER, "少しなら"),  # WATERING_LATER から三つ後。直近の外
        ]
        + [(one, "はい") for one in FILLERS[3:]]
    )
    _ = write(
        place / "claude", "d.jsonl", claude_code_lines("s4", WORKSPACE, later, first_minute=60)
    )
    _ = write(
        place / "claude",
        "e.jsonl",
        claude_code_tool_turn("s4", WORKSPACE, MOVIE, "調べました。古い名作ですね", minute=120),
    )
    return place


def _draft(
    place: Path, out: Path, judge: Judge, how: HowToRecall = TEST_HOW
) -> tuple[int, str, str]:
    written, errors = io.StringIO(), io.StringIO()
    real_stderr, sys.stderr = sys.stderr, errors
    try:
        code = Drafter(
            [place], out, judge=judge, default=fixed(CharacterPairs()), how=how, writing=written
        ).run()
    finally:
        sys.stderr = real_stderr
    return code, written.getvalue(), errors.getvalue()


def _read(out: Path) -> dict[str, object]:
    return tomllib.loads(out.read_text(encoding="utf-8"))


def _utterances(loaded: dict[str, object], key: str) -> list[str]:
    return [text_of(row, "utterance") for row in rows_of(loaded, key)]


def _out(tmp_path: Path) -> Path:
    out = tmp_path / "out" / "draft.toml"
    out.parent.mkdir()
    return out


class TestST030001:
    def test_ST_030_001_中身のある一往復だけが時刻順に並び二つの形が読まれる(
        self, tmp_path: Path
    ) -> None:
        out = _out(tmp_path)

        code, written, _ = _draft(_place(tmp_path), out, FixedJudge(SAFE))

        assert code == 0
        loaded = _read(out)
        spoken = _utterances(loaded, "exchange")
        assert spoken[:5] == [TOMATO, TAX, TAX_SOON, WATERING, BOOKS]
        assert NOD not in spoken and POINTING not in spoken and PASTED not in spoken
        assert POINTING_MATTER not in spoken
        assert not any("<" in one or "[Request" in one or "検査役" in one for one in spoken)
        assert spoken.count(TOMATO) == 1
        replies = {
            text_of(row, "utterance"): text_of(row, "reply") for row in rows_of(loaded, "exchange")
        }
        assert replies[MOVIE] == "調べました。古い名作ですね"
        assert "飛ばしたファイル 0" in written

    def test_ST_030_001_切れたファイルと時刻の壊れたファイルは飛ばされ数が出る(
        self, tmp_path: Path
    ) -> None:
        place = _place(tmp_path)
        broken = claude_code_lines(
            "s9", WORKSPACE, [("庭に花の種を蒔きました", "はい")], first_minute=200
        )
        _ = write(place / "claude", "broken.jsonl", broken + '{"type": "user", "sessionId": "s9"\n')
        bad_time = claude_code_lines("s8", WORKSPACE, [("犬の散歩に行きました", "はい")]).replace(
            "2026-01-01T09:00:00.000Z", "いつか"
        )
        _ = write(place / "claude", "badtime.jsonl", bad_time)
        out = _out(tmp_path)

        code, written, _ = _draft(place, out, FixedJudge(SAFE))

        assert code == 0
        spoken = _utterances(_read(out), "exchange")
        assert "庭に花の種を蒔きました" not in spoken and "犬の散歩に行きました" not in spoken
        assert "飛ばしたファイル 2" in written

    def test_ST_030_001_ファイルを指すと何も書かれず記録のディレクトリを指すよう返る(
        self, tmp_path: Path
    ) -> None:
        place = _place(tmp_path)
        out = tmp_path / "draft.toml"

        code, written, errors = _draft(place / "claude" / "a.jsonl", out, FixedJudge(SAFE))

        assert code == 1 and written == ""
        assert "はファイルです。記録のディレクトリを指してください" in errors
        assert not out.exists()

    @pytest.mark.parametrize("missing", [True, False])
    def test_ST_030_001_空と無いディレクトリでは何も書かれず理由が返る(
        self, tmp_path: Path, missing: bool
    ) -> None:
        place = tmp_path / "nothing"
        if not missing:
            place.mkdir()
        out = tmp_path / "draft.toml"

        code, written, errors = _draft(place, out, FixedJudge(SAFE))

        assert code == 1
        assert written == ""
        assert "下書きを作れません" in errors
        assert not out.exists()


class TestST030002:
    """どの発話が問になり、どれが覚えさせる側に残るか。"""

    def _drafted(self, tmp_path: Path, judge: Judge) -> dict[str, object]:
        out = _out(tmp_path)
        code, _, errors = _draft(_place(tmp_path), out, judge)
        assert code == 0, errors
        return _read(out)

    def test_ST_030_002_指すと判定された発話は問になり期待に前の発話が入る(
        self, tmp_path: Path
    ) -> None:
        loaded = self._drafted(tmp_path, FixedJudge(SAFE))

        cases = rows_of(loaded, "case")
        assert len(cases) == 1
        case = cases[0]
        assert text_of(case, "utterance") == TOMATO_LATER
        expected_names = names_of(case, "expected")
        exchanges = {
            text_of(row, "name"): text_of(row, "utterance") for row in rows_of(loaded, "exchange")
        }
        assert [exchanges[name] for name in expected_names] == [TOMATO]
        assert TOMATO_LATER not in exchanges.values()
        assert set(names_of(case, "overlap")) == set(expected_names)
        assert case["confirmed"] is False

    def test_ST_030_002_候補に上がっても指さない発話と候補の無い発話は覚えさせる側に残る(
        self, tmp_path: Path
    ) -> None:
        judge = FixedJudge(SAFE)

        loaded = self._drafted(tmp_path, judge)

        spoken = _utterances(loaded, "exchange")
        assert PARCEL in spoken and FOREX in spoken
        asked = {asking.utterance: asking.candidates for asking in judge.askings}
        assert set(asked[PARCEL]) & {TAX, TAX_SOON}  # 候補には上がった
        assert FOREX not in asked  # 候補が無いので判定に渡らない

    def test_ST_030_002_別の作業場所の発話は組にならない(self, tmp_path: Path) -> None:
        place = _place(tmp_path)
        elsewhere = "ベランダのトマトの苗に肥料をあげました"
        _ = write(
            place / "codex",
            "other.jsonl",
            codex_lines("s5", OTHER_WORKSPACE, [(elsewhere, "春です")], first_minute=100),
        )
        out = _out(tmp_path)

        code, _, _ = _draft(place, out, FixedJudge({elsewhere: [TOMATO], **SAFE}))

        assert code == 0
        assert _utterances(_read(out), "case") == [TOMATO_LATER]

    def test_ST_030_002_連鎖の真ん中は期待に残り問にならない(self, tmp_path: Path) -> None:
        loaded = self._drafted(
            tmp_path, FixedJudge({WATERING_LATER: [WATERING], FERTILIZER: [WATERING_LATER]})
        )

        assert _utterances(loaded, "case") == [FERTILIZER]
        assert WATERING_LATER in _utterances(loaded, "exchange")

    def test_ST_030_002_直近往復数以内の前の発話を指す組は出ない(self, tmp_path: Path) -> None:
        # TAX_SOON は TAX の直後で、思い出す手順が直近として渡すため候補に上がらない。
        judge = FixedJudge({**SAFE, TAX_SOON: [TAX]})

        loaded = self._drafted(tmp_path, judge)

        assert _utterances(loaded, "case") == [TOMATO_LATER]
        assert TAX_SOON not in {asking.utterance for asking in judge.askings}

    def test_ST_030_002_外した後の並びで直近に入る期待の組は出ない(self, tmp_path: Path) -> None:
        # 末尾の MOVIE の後に、遠くを指す問二つと MOVIE を指す問を並べる。三つが問として
        # 外れると、残る並びの末尾二つに MOVIE が入り、それを期待とする組は出ない。
        # MOVIE は思い出す時点では直近の外（間に問が二つ）なので候補には上がる。
        place = _place(tmp_path)
        far1 = "ベランダのトマトの苗を植えた話をもう一度聞かせてください"
        far2 = "住民税の納付書はもう払いましたか"
        near = "古い映画の題名は何でしたか"
        _ = write(
            place / "claude",
            "tail.jsonl",
            claude_code_lines(
                "s4", WORKSPACE, [(far1, "はい"), (far2, "はい"), (near, "はい")], first_minute=130
            ),
        )
        out = _out(tmp_path)
        judge = FixedJudge({far1: [TOMATO], far2: [TAX], near: [MOVIE]})

        code, _, errors = _draft(place, out, judge)

        assert code == 0, errors
        assert MOVIE in {c for a in judge.askings if a.utterance == near for c in a.candidates}
        assert _utterances(_read(out), "case") == [far1, far2]
        # 問にならなかった発話は覚えさせる側に残り、下書きから消えない。
        assert near in _utterances(_read(out), "exchange")


class TestST030003:
    def _measured(self, path: Path) -> tuple[int, str, str]:
        written, errors = io.StringIO(), io.StringIO()
        real_stderr, sys.stderr = sys.stderr, errors
        try:
            code = Measure(
                eval_path=path,
                baseline=HowToRecall(recent_turns=6, found_limit=5, relevance_floor=0.21),
                embeddings=Weighing(CharacterPairs(), clock=Steady()),
                writing=written,
            ).run()
        finally:
            sys.stderr = real_stderr
        return code, written.getvalue(), errors.getvalue()

    def _drafted(self, tmp_path: Path, judge: Judge | None = None) -> Path:
        out = _out(tmp_path)
        code, _, errors = _draft(_place(tmp_path), out, judge or FixedJudge(SAFE))
        assert code == 0, errors
        return out

    def _confirmed(self, out: Path) -> str:
        return out.read_text(encoding="utf-8").replace("confirmed = false", "confirmed = true")

    def test_ST_030_003_確かめていない問があると一問も測らない(self, tmp_path: Path) -> None:
        code, written, errors = self._measured(self._drafted(tmp_path))

        assert code == 1 and written == ""
        assert "確認していない問が 1 問" in errors

    def test_ST_030_003_全問を確認済みにすると測れる(self, tmp_path: Path) -> None:
        out = self._drafted(tmp_path)
        _ = out.write_text(self._confirmed(out), encoding="utf-8")

        code, written, _ = self._measured(out)

        assert code == 0
        assert "1問中" in written

    def test_ST_030_003_印を持たない問を手で足しても測れる(self, tmp_path: Path) -> None:
        out = self._drafted(tmp_path)
        text = self._confirmed(out) + "\n".join(
            [
                "",
                "[[case]]",
                'name = "hand"',
                'utterance = "納付書の期限はいつまででしたか"',
                'expected = ["e002"]',
                "forbidden = []",
                "",
            ]
        )
        _ = out.write_text(text, encoding="utf-8")

        code, written, _ = self._measured(out)

        assert code == 0
        assert "2問中" in written

    def test_ST_030_003_印を持たない架空の評価セットは今までどおり測れる(self) -> None:
        code, written, _ = self._measured(Path("evals/recall.toml"))

        assert code == 0
        assert "5問中" in written

    @pytest.mark.parametrize(
        ("kind", "old", "new"),
        [("問", 'name = "c002"', 'name = "c001"'), ("やりとり", 'name = "e002"', 'name = "e001"')],
    )
    def test_ST_030_003_名前が重なると一問も測らない(
        self, tmp_path: Path, kind: str, old: str, new: str
    ) -> None:
        out = self._drafted(tmp_path, FixedJudge({**SAFE, FERTILIZER: [WATERING_LATER]}))
        _ = out.write_text(self._confirmed(out).replace(old, new, 1), encoding="utf-8")

        code, written, errors = self._measured(out)

        assert code == 1 and written == ""
        assert f"{kind}の名前が重なっている" in errors


class TestST030004:
    def test_ST_030_004_リポジトリの配下には書かない(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        out = repo / "evals" / "draft.toml"
        out.parent.mkdir()

        code, written, errors = _draft(_place(tmp_path), out, FixedJudge(SAFE))

        assert code == 1 and written == ""
        assert "リポジトリの配下" in errors
        assert not out.exists()

    def test_ST_030_004_ディレクトリには書かない(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        out.mkdir()

        code, written, errors = _draft(_place(tmp_path), out, FixedJudge(SAFE))

        assert code == 1 and written == ""
        assert "はディレクトリです" in errors

    def test_ST_030_004_手元には書け原文が一致し画面に何で引いたかと数が出る(
        self, tmp_path: Path
    ) -> None:
        out = _out(tmp_path)

        code, written, _ = _draft(_place(tmp_path), out, FixedJudge(SAFE))

        assert code == 0
        pairs = {
            (text_of(row, "utterance"), text_of(row, "reply"))
            for row in rows_of(_read(out), "exchange")
        }
        assert pairs >= {(TOMATO, "いいですね"), (WATERING, "朝がおすすめです")}
        assert (
            "候補を引いた 埋め込み: AIモデル無し（character-pairs-v1） / 条件: "
            + "直近2往復・候補10件・下限0.15 / 判定: fixed-judge"
            in written
        )
        assert "記録: 3 セッション、中身のある発話 18 件（読めず飛ばしたファイル 0）" in written
        assert "覚えさせる発話: 17 件" in written
        assert "問: 1 問" in written and "すべて確認前" in written
        assert "confirmed = true にしてください" in written
        assert "手で足せます" in written and "直近の範囲の組は測れないので足しません" in written
        text = out.read_text(encoding="utf-8")
        assert "[covered]" in text and 'tool = "character-pairs"' in text and "ai_model" not in text

    def test_ST_030_004_同じ出力先へもう一度作ると上書きしない(self, tmp_path: Path) -> None:
        out = _out(tmp_path)
        _ = _draft(_place(tmp_path), out, FixedJudge(SAFE))
        before = out.read_text(encoding="utf-8")

        code, _, errors = _draft(
            _place(tmp_path), out, FixedJudge({**SAFE, FERTILIZER: [WATERING_LATER]})
        )

        assert code == 1
        assert "既にあります" in errors
        assert out.read_text(encoding="utf-8") == before

    def test_ST_030_004_使い方にどの記録がどの相手へ渡るかが書かれている(self) -> None:
        assert "別の相手へ渡ることになる" in USAGE
        assert "Claude Code へ渡す" in USAGE
        assert "記録を丸ごと渡すこともない" in USAGE
        assert "手で足す" in USAGE and "直近の範囲の組は測れないので足さない" in USAGE


class _Unavailable:
    @property
    def provenance(self) -> Provenance:
        return Provenance(ai_model=None, tool="unavailable", tool_version="v0")

    @property
    def name(self) -> str:
        return self.provenance.index_name

    def to_recall(self, text: str) -> Vector:

        return self.to_remember(text)

    def to_remember(self, text: str) -> Vector:
        del text
        raise EmbeddingsUnavailable(
            "意味を見る埋め込みを使えません。`uv sync` で依存を導入してください。"
        )


class TestST030008:
    def test_ST_030_008_問が一つも出なければ何も書かず理由が返る(self, tmp_path: Path) -> None:
        out = _out(tmp_path)

        code, written, errors = _draft(_place(tmp_path), out, FixedJudge({}))

        assert code == 1 and written == ""
        assert "問が一つも出ませんでした" in errors
        assert not out.exists()

    def test_ST_030_008_判定が続かなければ何も書かず理由が返る(self, tmp_path: Path) -> None:
        # 失敗の種類ごとの言い分けは、判定の実装に当てる IT-030-007 が確かめる。
        out = _out(tmp_path)

        code, written, errors = _draft(_place(tmp_path), out, FailingJudge("上限に当たった"))

        assert code == 1 and written == ""
        assert "上限に当たった" in errors
        assert not out.exists()

    def test_ST_030_008_埋め込みが使えなければ何も書かず導入の仕方が返る(
        self, tmp_path: Path
    ) -> None:
        out = _out(tmp_path)
        written, errors = io.StringIO(), io.StringIO()
        real_stderr, sys.stderr = sys.stderr, errors
        try:
            code = Drafter(
                [_place(tmp_path)],
                out,
                judge=FixedJudge(SAFE),
                default=fixed(_Unavailable()),
                writing=written,
            ).run()
        finally:
            sys.stderr = real_stderr

        assert code == 1 and written.getvalue() == ""
        assert "`uv sync`" in errors.getvalue()
        assert not out.exists()
