"""意味を見る埋め込み。手元で動かす。

語の重なりではなく意味を見る。AIモデルは手元で動き、提供元への鍵は要らない。
初回にAIモデルを取得するときだけ外へ繋ぐ。取得したAIモデルのファイルは
`YADORI_HOME` の下の `models/` に保存する。

既定の埋め込みである。INC-025 で候補を測って選んだ。導入は本体の依存に
含まれるため、`uv sync` で入る。

AIモデルを替えると、それまでのインデックスは使えない。インデックスは作った埋め込みの名前を持つため、
違う名前のインデックスは使わない。原文から作り直せば、以前の記憶も新しいAIモデルで探せる。
同じAIモデルでも、動かす道具の版が変わると数の並びの作り方が変わることがある。
そのため名前には道具の版も含め、版が上がればインデックスが作り直されるようにする。
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol, final

from yadori.domain.memory import EmbeddingsUnavailable, Vector

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
TOOL = "fastembed"
HOW_TO_INSTALL = "意味を見る埋め込みを使えません。`uv sync` で依存を導入してください。"


@final
class Multilingual:
    """意味を見る埋め込み。最初に使うときだけAIモデルを読み込む。"""

    def __init__(self, model: str = MODEL, cache_dir: Path | None = None) -> None:
        self._model: str = model
        self._cache_dir: Path | None = cache_dir
        self._loaded: _Embedder | None = None

    @property
    def name(self) -> str:
        # 道具の版で数の並びの作り方が変わるため、版まで名前に含める。
        short = self._model.split("/")[-1]
        return f"{short}/{TOOL}-{self._tool_version()}"

    def of(self, text: str) -> Vector:
        # 外の道具は数の並びへ型を付けないため、ここで受けて確かめる。
        for made in self._embedding().embed([text]):
            return tuple(float(value) for value in made)
        raise EmbeddingsUnavailable("埋め込みが返らなかった")

    def _tool_version(self) -> str:
        try:
            return version(TOOL)
        except PackageNotFoundError as missing:
            raise EmbeddingsUnavailable(HOW_TO_INSTALL) from missing

    def _embedding(self) -> _Embedder:
        """AIモデルを読み込む。使えなければ、何をすればよいかを添えて断る。"""
        if self._loaded is None:
            self._loaded = self._loading()
        return self._loaded

    def _loading(self) -> _Embedder:
        try:
            from fastembed import TextEmbedding
        except ImportError as missing:
            raise EmbeddingsUnavailable(HOW_TO_INSTALL) from missing
        try:
            with warnings.catch_warnings():
                # 道具が「以前の版と作り方が違う」と毎回告げるが、その差は名前に
                # 版を含めてインデックスを分けることで受けている。話す人には関係がない。
                warnings.filterwarnings("ignore", message=".*mean pooling.*", category=UserWarning)
                return TextEmbedding(
                    model_name=self._model,
                    cache_dir=None if self._cache_dir is None else str(self._cache_dir),
                )
        except Exception as trouble:
            raise EmbeddingsUnavailable(
                f"埋め込みのAIモデル {self._model} を使えません。"
                + f"初回は取得のため外へ繋がる必要があります。（{trouble}）"
            ) from trouble


class _Embedder(Protocol):
    """読み込んだAIモデルに求めること。測る道具の型をこの層の外へ出さない。"""

    def embed(self, documents: list[str]) -> Iterable[Iterable[float]]: ...
