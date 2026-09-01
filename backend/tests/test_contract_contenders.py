"""試す埋め込みの契約テスト。実物の変換版と道具の対応表に当てる。

AIモデルを読み込むため遅く、初回は取得のため外へ繋がる。通常の実行から分ける
（`just test-contract`）。ここで固定した観測値（配布元のカードにある例文の近さ、
数の並びの長さ、取得先のディレクトリの形、大きさ）は、変換版や道具の版が変わると
ずれる。ずれた時点で落ち、落ちたら測り直す。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from yadori.adapter.embedding import (
    Choosing,
    Closeness,
    Contender,
    Contenders,
    DefaultEmbeddings,
    Multilingual,
    NotAnEmbeddingName,
    Weighing,
)
from yadori.adapter.embedding.contenders import LISTED
from yadori.adapter.embedding.multilingual import MODEL
from yadori.domain.memory import HowToRecall

# 配布元のカードが公表している例文と近さ（ruri-v3-30m と ruri-v3-130m）。
CARD_SENTENCES = [
    "川べりでサーフボードを持った人たちがいます",
    "サーファーたちが川べりに立っています",
    "トピック: 瑠璃色のサーファー",
    "検索クエリ: 瑠璃色はどんな色？",
    "検索文書: 瑠璃色（るりいろ）は、紫みを帯びた濃い青。名は、半貴石の瑠璃（ラピスラズリ、"
    + "英: lapis lazuli）による。JIS慣用色名では「こい紫みの青」（略号 dp-pB）と"
    + "定義している[1][2]。",
]
CARD_CLOSENESS = {
    "ruri-v3-30m": {(0, 1): 0.9540, (0, 2): 0.8512, (3, 4): 0.9479},
    "ruri-v3-130m": {(0, 1): 0.9564, (0, 2): 0.8183, (3, 4): 0.9448},
}
PLANTED = "ベランダにトマトの苗を植えました"
PARAPHRASED = "植物の世話について教えてください"
UNRELATED = "為替の見通しはどうでしょうか"
# 既定の埋め込み（ruri-v3-30m、添え書き付き）で INC-031 に観測した値。下限 0.85 は
# 実際の会話の評価セットで決めた。同じ話題の言い換えでも 0.85 を下回るものがあり、
# 帯は薄い。道具の版や変換版が変わってずれれば落ちる。
FLOOR = HowToRecall().relevance_floor
FERTILIZER = "ベランダの野菜、そろそろ肥料をあげたほうがいいでしょうか"
FERTILIZER_CLOSENESS = 0.881
PARAPHRASED_CLOSENESS = 0.847
UNRELATED_CLOSENESS = 0.765
DEFAULT_NAME_HEAD = "sirasagi62/ruri-v3-30m-ONNX+0252b9d2/fastembed-0.8."


def _models_path() -> Path | None:
    home = os.environ.get("YADORI_HOME")
    return None if home is None else Path(home) / "models"


def _raw(name: str) -> Multilingual:
    """添え書きを付けずに動かす。カードの例文は文の中に作法の語を含んでいる。"""
    listed = Contenders().named(name)
    assert listed is not None
    Contenders().register(listed)
    return Multilingual(
        model=listed.source,
        ai_model=listed.source,
        cache_dir=_models_path(),
        source=listed.source,
        declared_gb=listed.declared_gb,
    )


@pytest.mark.contract
class TestContendersContract:
    @pytest.mark.parametrize("name", ["ruri-v3-30m", "ruri-v3-130m"])
    def test_ST_031_007_変換版がカードの近さと数の並びの長さを再現する(self, name: str) -> None:
        listed = Contenders().named(name)
        assert listed is not None
        embeddings = _raw(name)
        closeness = Closeness()

        made = [embeddings.to_remember(sentence) for sentence in CARD_SENTENCES]

        assert all(len(one) == listed.dim for one in made)
        assert closeness.between(made[0], made[0]) == pytest.approx(1.0, abs=1e-3)
        for (left, right), expected in CARD_CLOSENESS[name].items():
            assert closeness.between(made[left], made[right]) == pytest.approx(expected, abs=0.01)

    def test_ST_031_007_多言語の試す埋め込みは長さが表の値で言い換えが無関係より近い(self) -> None:
        listed = Contenders().named("multilingual-e5-small")
        assert listed is not None
        picked = Choosing(_models_path()).pick("multilingual-e5-small")
        assert isinstance(picked, Weighing)
        closeness = Closeness()

        planted = picked.to_remember(PLANTED)
        paraphrased = closeness.between(planted, picked.to_recall(PARAPHRASED))
        unrelated = closeness.between(planted, picked.to_recall(UNRELATED))

        assert len(planted) == listed.dim
        assert paraphrased > unrelated

    @pytest.mark.parametrize("listed", LISTED, ids=[one.name for one in LISTED])
    def test_IT_031_005_支度が取得先のディレクトリを探し当て大きさが配布元の表示に収まる(
        self, listed: Contender
    ) -> None:
        declared = listed.declared_gb
        notes: list[str] = []
        picked = Choosing(_models_path(), announcing=notes.append).pick(listed.name)
        assert isinstance(picked, Weighing)

        _ = picked.to_remember(PLANTED)
        weight = picked.weighed()

        assert weight.size.bytes is not None
        assert 0.8 * declared <= weight.size.bytes / 1e9 <= 1.2 * declared
        assert weight.loaded_in is not None and weight.loaded_in > 0
        # 取得済みが前提。前触れが出れば取得先の探し方が変わっている。
        assert notes == []

    def test_IT_031_005_既定の埋め込みも取得先のディレクトリを探し当てる(self) -> None:
        prepared = DefaultEmbeddings()(_models_path()).prepare()

        assert prepared.size.bytes is not None
        assert 0.8 * 0.15 <= prepared.size.bytes / 1e9 <= 1.2 * 0.15

    def test_ST_031_004_既定の名前と近さが固定した観測と一致し下限が意味を持つ(self) -> None:
        embeddings = DefaultEmbeddings()(_models_path())
        closeness = Closeness()

        planted = embeddings.to_remember(PLANTED)
        fertilizer = closeness.between(planted, embeddings.to_recall(FERTILIZER))
        paraphrased = closeness.between(planted, embeddings.to_recall(PARAPHRASED))
        unrelated = closeness.between(planted, embeddings.to_recall(UNRELATED))

        assert embeddings.name.startswith(DEFAULT_NAME_HEAD)
        assert len(planted) == 256
        assert fertilizer == pytest.approx(FERTILIZER_CLOSENESS, abs=0.01)
        assert paraphrased == pytest.approx(PARAPHRASED_CLOSENESS, abs=0.01)
        assert unrelated == pytest.approx(UNRELATED_CLOSENESS, abs=0.01)
        assert fertilizer > FLOOR > unrelated

    def test_ST_031_006_取得済みの試す埋め込みは鍵が無く外へ繋がらなくても測れる(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
        picked = Choosing(_models_path()).pick("ruri-v3-30m")
        assert isinstance(picked, Weighing)

        made = picked.to_remember(PLANTED)

        assert len(made) == 256

    def test_ST_031_006_実物の対応表でも既定の名前は指せ無い名前は取得せずに断られる(self) -> None:
        choosing = Choosing(_models_path())

        in_table = choosing.pick(MODEL)
        with pytest.raises(NotAnEmbeddingName):
            _ = choosing.pick("nobody/nothing-at-all")

        assert isinstance(in_table, Weighing)
