"""INC-031 のシステムテスト。試す埋め込みを名前で指して測り、結果に出自と添え書きと重さが並ぶ。

架空の会話で書く。AIモデルは読み込まず、道具の代わりに語の重なりの並びを返すものを差し込む。
実物に当てる確かめは契約テスト（`test_contract_contenders.py`）に置く。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from tests.contending import RURI, Heavy, Runner
from tests.sora import Steady, fixed
from tests.test_st_018_measure import BASELINE, EVAL, TIGHTER
from yadori.adapter.embedding import Choosing, Weighing
from yadori.domain.memory import EmbeddingsUnavailable, Provenance, Vector
from yadori.infrastructure.draft import Drafter
from yadori.infrastructure.entry import Entry
from yadori.infrastructure.measure import Measure

MINI = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class _Missing:
    @property
    def provenance(self) -> Provenance:
        return Provenance(None, "missing", "v0")

    @property
    def name(self) -> str:
        return self.provenance.index_name

    def to_remember(self, text: str) -> Vector:
        del text
        raise EmbeddingsUnavailable(
            "意味を見る埋め込みを使えません。`uv sync` で依存を導入してください。"
        )

    def to_recall(self, text: str) -> Vector:
        return self.to_remember(text)


def _eval_at(tmp_path: Path) -> Path:
    path = tmp_path / "recall.toml"
    _ = path.write_text(EVAL, encoding="utf-8")
    return path


def _entry(tmp_path: Path, written: str | None, notes: list[str]) -> tuple[int, str, str]:
    """外の入口から測る。名前で選ぶ部品には道具の代わりと使い捨ての取得先を渡す。"""
    choosing = Choosing(tmp_path / "models", announcing=notes.append, runner=Runner)
    argv = ["yadori", "measure", "--eval", str(_eval_at(tmp_path))]
    if written is not None:
        argv += ["--embedding", written]
    out, err = io.StringIO(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = Entry(argv, choosing=choosing).run()
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return code, out.getvalue(), err.getvalue()


class TestST031001:
    """どの名前がどこへ解決するか。"""

    @pytest.mark.parametrize(
        ("written", "shown"),
        [
            (None, "埋め込み: sirasagi62/ruri-v3-30m-ONNX（fastembed-"),
            ("characters", "埋め込み: AIモデル無し（character-pairs-v1） 添え書き: 無し"),
            ("multilingual", "埋め込み: paraphrase-multilingual-MiniLM-L12-v2（fastembed-"),
            (MINI, "埋め込み: paraphrase-multilingual-MiniLM-L12-v2（fastembed-"),
            (
                "ruri-v3-30m",
                "埋め込み: sirasagi62/ruri-v3-30m-ONNX（fastembed-",
            ),
        ],
    )
    def test_ST_031_001_指せる名前は測れて出自が出る(
        self, tmp_path: Path, written: str | None, shown: str
    ) -> None:
        code, out, _ = _entry(tmp_path, written, [])

        assert code == 0
        assert shown in out
        assert "2問中" in out
        if written is None:
            # 引数無しの既定の行は、測り直した代表の下限を示す（SPEC-031-003）。
            assert "条件: 直近6往復・上限5件・下限0.85" in out

    def test_ST_031_001_添え書きを定める試す埋め込みは行に両側の語が出る(
        self, tmp_path: Path
    ) -> None:
        _, out, _ = _entry(tmp_path, "ruri-v3-30m", [])

        assert "添え書き: 覚える「検索文書: 」 問い合わせ「検索クエリ: 」" in out

    def test_ST_031_001_二つの道は出自が二つ出る(self, tmp_path: Path) -> None:
        code, out, _ = _entry(tmp_path, "characters+multilingual", [])

        assert code == 0
        assert "埋め込み: AIモデル無し（character-pairs-v1）" in out
        assert "埋め込み: paraphrase-multilingual-MiniLM-L12-v2（fastembed-" in out

    @pytest.mark.parametrize("written", ["ruri", "nobody/nothing"])
    def test_ST_031_001_指せない名前は何も取得せず理由に指せる名前が並ぶ(
        self, tmp_path: Path, written: str
    ) -> None:
        code, out, err = _entry(tmp_path, written, [])

        assert code == 1
        assert out == ""
        assert f"測れません: {written} は指せる埋め込みではありません" in err
        assert "characters、multilingual、ruri-v3-30m、ruri-v3-130m、multilingual-e5-small" in err
        assert "配布元/名前 で指せます" in err
        assert not (tmp_path / "models").exists()


class TestST031006:
    """手元に無いときの断り方と取得の告げ方（自動の分）。"""

    def test_ST_031_006_使えない埋め込みは測る前に理由が返り何を導入すればよいか分かる(
        self, tmp_path: Path
    ) -> None:
        written = io.StringIO()
        errors = io.StringIO()
        real = sys.stderr
        sys.stderr = errors
        try:
            code = Measure(
                Choosing(tmp_path / "models", default=fixed(_Missing())).pick(None),
                eval_path=_eval_at(tmp_path),
                writing=written,
            ).run()
        finally:
            sys.stderr = real

        assert code == 1
        assert written.getvalue() == ""
        assert "測れません" in errors.getvalue()
        assert "`uv sync` で依存を導入してください" in errors.getvalue()

    def test_ST_031_006_下書きの入口も道具が無ければ理由を書いて終わる(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 道具を読み込めない状態を、取り込みが失敗する形で作る。工場は差し込まず本物のまま。
        monkeypatch.setitem(sys.modules, "fastembed", None)
        monkeypatch.setitem(sys.modules, "fastembed.common.model_description", None)
        monkeypatch.setenv("YADORI_HOME", str(tmp_path / "home"))
        errors = io.StringIO()
        real = sys.stderr
        sys.stderr = errors
        try:
            code = Drafter(
                [tmp_path / "records"], tmp_path / "draft.toml", writing=io.StringIO()
            ).run()
        finally:
            sys.stderr = real

        assert code == 1
        assert "下書きを作れません" in errors.getvalue()
        assert "`uv sync` で依存を導入してください" in errors.getvalue()
        assert not (tmp_path / "draft.toml").exists()

    def test_ST_031_006_未取得なら前触れが出てから取得が始まる(self, tmp_path: Path) -> None:
        notes: list[str] = []

        code, _, _ = _entry(tmp_path, "ruri-v3-30m", notes)

        assert code == 0
        assert notes == [
            "取得します: sirasagi62/ruri-v3-30m-ONNX 約0.15GB（配布元の表示） → "
            + str(tmp_path / "models")
        ]


class TestST031002:
    """結果の冒頭で出自と重さが読め、読み込みは一度だけ。"""

    def _written(self, tmp_path: Path, changed: object = None) -> str:
        written = io.StringIO()
        heavy = Heavy(loaded_in=1.5)
        code = Measure(
            Weighing(heavy, clock=Steady()),
            eval_path=_eval_at(tmp_path),
            baseline=BASELINE,
            changed=TIGHTER if changed else None,
            writing=written,
        ).run()
        assert code == 0
        assert heavy.prepared == 1
        return written.getvalue()

    def test_ST_031_002_二度測ると埋め込みの行が二度出て読み込みは一度で一発話に含まれない(
        self, tmp_path: Path
    ) -> None:
        both = self._written(tmp_path, changed=True)

        assert both.count("埋め込み: heavy（fake-tool-v1） 添え書き: 無し") == 2
        assert both.count("大きさ 0.15GB / 読み込み 1.500秒 / 一発話 0.001秒") == 2

    def test_ST_031_002_冒頭は出自と添え書きの行と大きさと時間の行そして条件の順(
        self, tmp_path: Path
    ) -> None:
        lines = self._written(tmp_path).splitlines()

        assert lines[0].startswith("埋め込み: heavy（fake-tool-v1）")
        assert lines[1].startswith("大きさ 0.15GB / 読み込み 1.500秒")
        assert lines[2].startswith("条件: ")


class TestST031005:
    """添え書きが側ごとに行に出る。"""

    def test_ST_031_005_定めるものは両側が別々に出て定めないものは無し(
        self, tmp_path: Path
    ) -> None:
        with_prefixes, without = io.StringIO(), io.StringIO()
        path = _eval_at(tmp_path)

        _ = Measure(
            Weighing(Heavy(prefixes=RURI), clock=Steady()), eval_path=path, writing=with_prefixes
        ).run()
        _ = Measure(Weighing(Heavy(), clock=Steady()), eval_path=path, writing=without).run()

        assert (
            "添え書き: 覚える「検索文書: 」 問い合わせ「検索クエリ: 」" in with_prefixes.getvalue()
        )
        assert "添え書き: 無し" in without.getvalue()
