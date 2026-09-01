"""埋め込みを動かす道具の対応表。

道具が名前で動かせる AIモデルの一覧。登録した試す埋め込みも載る。名前が載って
いるか、配布元の置き場と配布元の表示の大きさは何か、をここで聞く。何も取得しない。
道具そのものが無ければ、何をすればよいかを添えて断る。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from yadori.domain.memory import EmbeddingsUnavailable

HOW_TO_INSTALL = "意味を見る埋め込みを使えません。`uv sync` で依存を導入してください。"


@dataclass(frozen=True)
class Described:
    """対応表の一件の申告。配布元の置き場（無いものもある）と、配布元の表示の大きさ。"""

    source: str | None
    declared_gb: float | None


@final
class Table:
    def described(self, model: str) -> Described | None:
        """名前が載っていればその申告。載っていなければ無し。"""
        for known in self._listed():
            if known.get("model") == model:
                return Described(source=self._source_of(known), declared_gb=self._size_of(known))
        return None

    def _source_of(self, known: dict[str, object]) -> str | None:
        # 道具の一覧は型を付けないため、ここで受けて確かめる。
        sources = known.get("sources")
        if not isinstance(sources, dict):
            return None
        source = sources.get("hf")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return source if isinstance(source, str) else None

    def _size_of(self, known: dict[str, object]) -> float | None:
        size = known.get("size_in_GB")
        return float(size) if isinstance(size, int | float) else None

    def _listed(self) -> list[dict[str, object]]:
        try:
            from fastembed import TextEmbedding
        except ImportError as missing:
            raise EmbeddingsUnavailable(HOW_TO_INSTALL) from missing
        return TextEmbedding.list_supported_models()
