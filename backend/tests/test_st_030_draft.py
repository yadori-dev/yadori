"""INC-030 のシステムテスト。外の入口（下書きを作る、測る）から確かめる。

記録は架空の会話で作る。判定は答えを固定したものに差し替える。
"""

from __future__ import annotations

import io
import sys
import tomllib
from pathlib import Path

import pytest

from tests.records import (
    OTHER_WORKSPACE,
    WORKSPACE,
    FailingJudge,
    FixedJudge,
    claude_code_lines,
    claude_code_noise,
    codex_lines,
    codex_noise,
    names_of,
    rows_of,
    text_of,
    write,
)
from yadori.adapter.embedding import CharacterPairs
from yadori.domain.evaluation import Judge
from yadori.domain.memory import HowToRecall
from yadori.infrastructure.draft import Drafter
from yadori.infrastructure.entry import USAGE
from yadori.infrastructure.measure import Measure

TOMATO = "ベランダにトマトの苗を植えました"
WATERING = "水やりは朝と夕方どちらがいいですか"
TAX = "住民税の納付書が届きました"
BOOKS = "図書館で小説を三冊借りました"
MOVIE = "昨日は古い映画を観ました"
PLANTS = "植物の世話について教えてください"
FERTILIZER = "庭の野菜に肥料をあげる時期はいつですか"
NOD = "いいよ"
POINTING = "それ、どうなった？"
FILLERS = [
    "新しい鍵盤楽器が届きました",
    "洗濯物がよく乾きました",
    "歯医者の予約を来週に取りました",
    "近所で工事が始まるそうです",
    "豆を挽いて珈琲を淹れました",
    "自転車のタイヤに空気を入れました",
    "窓を拭いたら部屋が明るくなりました",
]
# 期待が末尾の直近に入らない、いつも通る組。
SAFE = {PLANTS: [TOMATO]}


def _place(tmp_path: Path) -> Path:
    """二つの形式の記録と雑音を置いた置き場。"""
    place = tmp_path / "records"
    turns = [(TOMATO, "いいですね"), (NOD, "はい"), (TAX, "期限をお忘れなく"), (POINTING, "はい")]
    _ = write(place / "claude", "a.jsonl", claude_code_lines("s1", WORKSPACE, turns))
    _ = write(place / "claude", "noise.jsonl", claude_code_noise("s2", WORKSPACE, minute=30))
    codex_turns = [(WATERING, "朝がおすすめです"), (BOOKS, "楽しみですね"), (TOMATO, "重複です")]
    _ = write(
        place / "codex", "b.jsonl", codex_lines("s3", WORKSPACE, codex_turns, first_minute=40)
    )
    _ = write(place / "codex", "c.jsonl", codex_noise("s3", WORKSPACE, minute=50))
    later = [(PLANTS, "トマトの件ですね")] + [(one, "はい") for one in FILLERS]
    _ = write(
        place / "claude", "d.jsonl", claude_code_lines("s4", WORKSPACE, later, first_minute=60)
    )
    return place


def _draft(place: Path, out: Path, judge: Judge) -> tuple[int, str, str]:
    written, errors = io.StringIO(), io.StringIO()
    real_stderr, sys.stderr = sys.stderr, errors
    try:
        code = Drafter([place], out, judge=judge, writing=written).run()
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
        spoken = _utterances(_read(out), "exchange")
        assert spoken[:4] == [TOMATO, TAX, WATERING, BOOKS]
        assert NOD not in spoken and POINTING not in spoken
        assert not any("<" in one or "[Request" in one for one in spoken)
        assert spoken.count(TOMATO) == 1
        assert "飛ばしたファイル 0" in written

    def test_ST_030_001_切れたファイルは飛ばされ数が出る(self, tmp_path: Path) -> None:
        place = _place(tmp_path)
        broken = claude_code_lines("s9", WORKSPACE, [(MOVIE, "はい")], first_minute=200)
        _ = write(place / "claude", "broken.jsonl", broken + '{"type": "user", "sessionId": "s9"\n')
        out = _out(tmp_path)

        code, written, _ = _draft(place, out, FixedJudge(SAFE))

        assert code == 0
        assert MOVIE not in _utterances(_read(out), "exchange")
        assert "飛ばしたファイル 1" in written

    @pytest.mark.parametrize("missing", [True, False])
    def test_ST_030_001_空と無い置き場では何も書かれず理由が返る(
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
    """どの発話が件になり、どれが覚えさせる側に残るか。"""

    def _drafted(self, tmp_path: Path, judge: Judge) -> dict[str, object]:
        out = _out(tmp_path)
        code, _, errors = _draft(_place(tmp_path), out, judge)
        assert code == 0, errors
        return _read(out)

    def test_ST_030_002_指すと判定された発話は件になり期待に前の発話が入る(
        self, tmp_path: Path
    ) -> None:
        loaded = self._drafted(tmp_path, FixedJudge({PLANTS: [TOMATO, WATERING], MOVIE: [TAX]}))

        cases = rows_of(loaded, "case")
        assert len(cases) == 1
        case = cases[0]
        assert text_of(case, "utterance") == PLANTS
        expected_names = names_of(case, "expected")
        exchanges = {
            text_of(row, "name"): text_of(row, "utterance") for row in rows_of(loaded, "exchange")
        }
        assert [exchanges[name] for name in expected_names] == [TOMATO, WATERING]
        assert PLANTS not in exchanges.values()
        assert set(names_of(case, "overlap")) == set(expected_names)
        assert case["confirmed"] is False

    def test_ST_030_002_語も意味も遠くても道具が指すと答えれば件になる(
        self, tmp_path: Path
    ) -> None:
        loaded = self._drafted(tmp_path, FixedJudge({FILLERS[6]: [TAX]}))

        assert _utterances(loaded, "case") == [FILLERS[6]]

    def test_ST_030_002_別の作業場所の発話は組にならない(self, tmp_path: Path) -> None:
        place = _place(tmp_path)
        _ = write(
            place / "codex",
            "other.jsonl",
            codex_lines("s5", OTHER_WORKSPACE, [(FERTILIZER, "春です")], first_minute=100),
        )
        out = _out(tmp_path)

        code, _, _ = _draft(place, out, FixedJudge({FERTILIZER: [TOMATO], **SAFE}))

        assert code == 0
        assert _utterances(_read(out), "case") == [PLANTS]

    def test_ST_030_002_同じセッションで直前の発話を指す組は出ない(self, tmp_path: Path) -> None:
        # TAX は TOMATO と同じセッションの二つ後。実際の会話では直近が渡す。
        loaded = self._drafted(tmp_path, FixedJudge({**SAFE, TAX: [TOMATO]}))

        assert _utterances(loaded, "case") == [PLANTS]

    def test_ST_030_002_連鎖の真ん中は期待に残り件にならない(self, tmp_path: Path) -> None:
        loaded = self._drafted(tmp_path, FixedJudge({WATERING: [TOMATO], PLANTS: [WATERING]}))

        assert _utterances(loaded, "case") == [PLANTS]
        assert WATERING in _utterances(loaded, "exchange")

    def test_ST_030_002_外した後の並びで直近に入る期待の組は出ない(self, tmp_path: Path) -> None:
        # 件を外す前は FILLERS[0] の前に PLANTS が居て直近の外だが、PLANTS が件になると
        # 残る並びの末尾六つに FILLERS[0] が入る。
        loaded = self._drafted(tmp_path, FixedJudge({**SAFE, FILLERS[6]: [FILLERS[0]]}))

        assert _utterances(loaded, "case") == [PLANTS]


class TestST030003:
    def _measured(self, path: Path) -> tuple[int, str, str]:
        written, errors = io.StringIO(), io.StringIO()
        real_stderr, sys.stderr = sys.stderr, errors
        try:
            code = Measure(
                eval_path=path,
                baseline=HowToRecall(recent_turns=6, found_limit=5, relevance_floor=0.21),
                embeddings=CharacterPairs(),
                writing=written,
            ).run()
        finally:
            sys.stderr = real_stderr
        return code, written.getvalue(), errors.getvalue()

    def _drafted(self, tmp_path: Path) -> Path:
        out = _out(tmp_path)
        code, _, errors = _draft(_place(tmp_path), out, FixedJudge(SAFE))
        assert code == 0, errors
        return out

    def _confirmed(self, out: Path) -> str:
        return out.read_text(encoding="utf-8").replace("confirmed = false", "confirmed = true")

    def test_ST_030_003_確かめていない件があると一件も測らない(self, tmp_path: Path) -> None:
        code, written, errors = self._measured(self._drafted(tmp_path))

        assert code == 1 and written == ""
        assert "確認していない件が 1 件" in errors

    def test_ST_030_003_全件を確認済みにすると測れる(self, tmp_path: Path) -> None:
        out = self._drafted(tmp_path)
        _ = out.write_text(self._confirmed(out), encoding="utf-8")

        code, written, _ = self._measured(out)

        assert code == 0
        assert "1件中" in written

    def test_ST_030_003_印を持たない件を手で足しても測れる(self, tmp_path: Path) -> None:
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
        assert "2件中" in written

    def test_ST_030_003_印を持たない架空の評価セットは今までどおり測れる(self) -> None:
        code, written, _ = self._measured(Path("evals/recall.toml"))

        assert code == 0
        assert "5件中" in written

    @pytest.mark.parametrize(
        ("kind", "old", "new"),
        [("件", 'name = "c002"', 'name = "c001"'), ("やりとり", 'name = "e002"', 'name = "e001"')],
    )
    def test_ST_030_003_名前が重なると一件も測らない(
        self, tmp_path: Path, kind: str, old: str, new: str
    ) -> None:
        out = _out(tmp_path)
        code, _, errors = _draft(_place(tmp_path), out, FixedJudge({**SAFE, FILLERS[6]: [TAX]}))
        assert code == 0, errors
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
        assert "ディレクトリ" in errors

    def test_ST_030_004_手元には書け原文が一致し画面に数が出る(self, tmp_path: Path) -> None:
        out = _out(tmp_path)

        code, written, _ = _draft(_place(tmp_path), out, FixedJudge(SAFE))

        assert code == 0
        pairs = {
            (text_of(row, "utterance"), text_of(row, "reply"))
            for row in rows_of(_read(out), "exchange")
        }
        assert pairs >= {(TOMATO, "いいですね"), (WATERING, "朝がおすすめです")}
        assert "記録: 3 セッション、中身のある発話 12 件（読めず飛ばしたファイル 0）" in written
        assert "覚えさせる発話: 11 件" in written
        assert "件: 1 件" in written and "すべて確認前" in written
        assert "confirmed = true にしてください" in written

    def test_ST_030_004_同じ出力先へもう一度作ると上書きしない(self, tmp_path: Path) -> None:
        out = _out(tmp_path)
        _ = _draft(_place(tmp_path), out, FixedJudge(SAFE))
        before = out.read_text(encoding="utf-8")

        code, _, errors = _draft(_place(tmp_path), out, FixedJudge({**SAFE, MOVIE: [TAX]}))

        assert code == 1
        assert "既にあります" in errors
        assert out.read_text(encoding="utf-8") == before

    def test_ST_030_004_使い方にどの記録がどの相手へ渡るかが書かれている(self) -> None:
        assert "Codex の記録は判定のために別の相手へ渡る" in USAGE
        assert "Claude Code へ渡る" in USAGE


class TestST030007:
    def test_ST_030_007_埋め込みの道具が無くても下書きは作れる(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "fastembed", None)
        out = _out(tmp_path)

        code, _, errors = _draft(_place(tmp_path), out, FixedJudge(SAFE))

        assert code == 0, errors
        assert out.exists()


class TestST030008:
    @pytest.mark.parametrize(
        "reason",
        [
            "対話する道具を呼べなかった",
            "途中で失敗した",
            "利用の上限に当たった",
            "大きすぎて受け付けられなかった。置き場を絞って指し直すと通ることがあります",
        ],
    )
    def test_ST_030_008_判定が続かなければ何も書かず理由が返る(
        self, tmp_path: Path, reason: str
    ) -> None:
        out = _out(tmp_path)

        code, written, errors = _draft(_place(tmp_path), out, FailingJudge(reason))

        assert code == 1 and written == ""
        assert reason in errors
        assert not out.exists()
