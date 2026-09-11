"""意味を見る埋め込みの契約テスト。実物のAIモデルに当てる。

`HowToRecall` の下限 0.50 は、このAIモデルと道具の版で測って決めた値である。道具の
版が上がって数の並びの作り方が変わると、下限の意味も変わる。ここで固定した
観測値と実物を突き合わせ、ずれた時点で落ちるようにする。落ちたら測り直す。

AIモデルを読み込むため遅く、初回は取得のため外へ繋がる。通常の実行から分ける
（`just test-contract`）。`ST-025-004` の「鍵が無く外へ繋がらなくても思い出せる」
は、取得済みのAIモデルで通ることをここで確かめる。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from yadori.adapter.embedding import Closeness, Multilingual

PLANTED = "ベランダにトマトの苗を植えました"
PARAPHRASED = "植物の世話について教えてください"
UNRELATED = "為替の見通しはどうでしょうか"

# INC-025 で観測した値。言い換えは下限 0.50 を越え、無関係は越えない。
PARAPHRASED_CLOSENESS = 0.617
FLOOR = 0.50
DIMENSIONS = 384


def _models_path() -> Path | None:
    home = os.environ.get("YADORI_HOME")
    return None if home is None else Path(home) / "models"


@pytest.mark.contract
class TestMultilingualContract:
    def test_数の並びの長さと名前が固定した観測と一致する(self) -> None:
        embeddings = Multilingual(cache_dir=_models_path())

        made = embeddings.to_remember(PLANTED)

        assert len(made) == DIMENSIONS
        assert embeddings.name.startswith("paraphrase-multilingual-MiniLM-L12-v2/fastembed-0.8.")

    def test_言い換えの近さが下限を越え無関係は越えない(self) -> None:
        embeddings = Multilingual(cache_dir=_models_path())
        closeness = Closeness()

        planted = embeddings.to_remember(PLANTED)
        paraphrased = closeness.between(planted, embeddings.to_remember(PARAPHRASED))
        unrelated = closeness.between(planted, embeddings.to_remember(UNRELATED))

        assert paraphrased == pytest.approx(PARAPHRASED_CLOSENESS, abs=0.02)
        assert paraphrased > FLOOR
        assert unrelated < FLOOR

    def test_鍵を持たず外へ繋がらない前提で使える(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 取得済みのAIモデルは鍵無しで読める。提供元の鍵が環境に無いことも確かめる。
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HF_TOKEN", raising=False)

        made = Multilingual(cache_dir=_models_path()).to_remember(PLANTED)

        assert len(made) == DIMENSIONS
