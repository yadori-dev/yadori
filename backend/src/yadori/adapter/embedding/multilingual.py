"""意味を見る埋め込み。手元で動かす。

語の重なりではなく意味を見る。模型は手元で動き、提供元への鍵は要らない。
初回に模型を取得するときだけ外へ繋ぐ。

**いまは候補を測るための実装であり、既定ではない。** INC-025 で測ったところ、
実際の会話では語の重なりを見る実装に及ばなかった。使うには測る道具の側の
導入（`uv sync --all-extras`）が要る。

模型を替えると、それまでの索引は使えない。索引は作った模型の名前を持つため、
違う名前の索引は使わない。原文から作り直せば、以前の記憶も新しい模型で探せる。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, final

from yadori.domain.memory import EmbeddingsUnavailable, Vector

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@final
class Multilingual:
    """意味を見る埋め込み。最初に使うときだけ模型を読み込む。"""

    def __init__(self, model: str = MODEL) -> None:
        self._model: str = model
        self._loaded: object | None = None

    @property
    def name(self) -> str:
        return self._model.split("/")[-1]

    def of(self, text: str) -> Vector:
        # 外の道具は数の並びへ型を付けないため、ここで受けて確かめる。
        for made in self._embedding().embed([text]):
            return tuple(float(value) for value in made)
        raise EmbeddingsUnavailable("埋め込みが返らなかった")

    def _embedding(self) -> _Embedder:
        """模型を読み込む。使えなければ、何をすればよいかを添えて断る。"""
        if self._loaded is None:
            self._loaded = self._loading()
        return self._loaded  # pyright: ignore[reportReturnType]

    def _loading(self) -> object:
        try:
            from fastembed import TextEmbedding
        except ImportError as missing:
            raise EmbeddingsUnavailable(
                "意味を見る埋め込みを使えません。`uv sync --all-extras` で"
                + "測る道具を導入してください。"
            ) from missing
        try:
            return TextEmbedding(model_name=self._model)
        except Exception as trouble:
            raise EmbeddingsUnavailable(
                f"埋め込みの模型 {self._model} を使えません。"
                + f"初回は取得のため外へ繋がる必要があります。（{trouble}）"
            ) from trouble


class _Embedder(Protocol):
    """読み込んだ模型に求めること。測る道具の型をこの層の外へ出さない。"""

    def embed(self, documents: list[str]) -> Iterable[Iterable[float]]: ...
