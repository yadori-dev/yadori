"""試す埋め込みの一覧。

道具の対応表に無い AIモデルのうち、ONNX 形式で配られていて名前で指せるもの。
一件ごとに、指す名前、配布元の置き場、ONNX のファイル名、数の並びのまとめ方と
正規化、数の並びの長さ、配布元の表示の大きさ、添え書きの組を持つ。

道具への登録は解けた一件だけをその直前に行う。全件を先に登録すると、登録した
置き場を `配布元/名前` で指したときに対応表の段で解けて、添え書き無しの同じ
AIモデルが返る裏口ができる。登録済みかは道具の一覧で確かめ、隠れた状態を持たない。

大きさと数の並びの長さは配布元の表示である。INC-031 で選んだ。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from yadori.adapter.embedding.table import HOW_TO_INSTALL, Table
from yadori.domain.memory import EmbeddingsUnavailable, Prefixes

# 検索の作法。ruri-v3 は覚える側に文書、問い合わせ側にクエリの語を付ける。
RURI_RETRIEVAL = Prefixes(remember="検索文書: ", recall="検索クエリ: ")
# multilingual-e5 は検索以外の用途で両側に query を付ける作法である。
E5_QUERY = Prefixes(remember="query: ", recall="query: ")


@dataclass(frozen=True)
class Contender:
    """試す埋め込みの一件。`source` は配布元の置き場で、出自の AIモデルの名前にもなる。"""

    name: str
    source: str
    model_file: str
    dim: int
    declared_gb: float
    prefixes: Prefixes | None

    @property
    def ai_model(self) -> str:
        # 同じ元の AIモデルの別の変換版と区別するため、配布元を含めた名前を出自に使う。
        return self.source


LISTED: tuple[Contender, ...] = (
    Contender(
        name="ruri-v3-30m",
        source="sirasagi62/ruri-v3-30m-ONNX",
        model_file="onnx/model.onnx",
        dim=256,
        declared_gb=0.15,
        prefixes=RURI_RETRIEVAL,
    ),
    Contender(
        name="ruri-v3-130m",
        source="sirasagi62/ruri-v3-130m-ONNX",
        model_file="onnx/model.onnx",
        dim=512,
        declared_gb=0.53,
        prefixes=RURI_RETRIEVAL,
    ),
    Contender(
        name="multilingual-e5-small",
        source="intfloat/multilingual-e5-small",
        model_file="onnx/model.onnx",
        dim=384,
        declared_gb=0.47,
        prefixes=E5_QUERY,
    ),
)


@final
class Contenders:
    """試す埋め込みの一覧。名前でも配布元の置き場でも引ける。"""

    def __init__(self) -> None:
        self._listed: tuple[Contender, ...] = LISTED

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(one.name for one in self._listed)

    def named(self, name: str) -> Contender | None:
        """名前か配布元の置き場で一件を引く。無ければ無し。

        置き場でも引くのは、一覧にある AIモデルを `配布元/名前` で指されたときも添え書き
        付きで返すためである。
        """
        for one in self._listed:
            if name in (one.name, one.source):
                return one
        return None

    def register(self, contender: Contender) -> None:
        """道具へ登録する。登録済みなら何もしない。道具が無ければ何をすればよいかを添えて断る。"""
        if Table().described(contender.source) is not None:
            return
        try:
            from fastembed import TextEmbedding
            from fastembed.common.model_description import ModelSource, PoolingType
        except ImportError as missing:
            raise EmbeddingsUnavailable(HOW_TO_INSTALL) from missing
        TextEmbedding.add_custom_model(
            model=contender.source,
            # 一覧の三件はいずれも、語ごとの並びを平均して長さ 1 に揃える作りである。
            pooling=PoolingType.MEAN,
            normalization=True,
            sources=ModelSource(hf=contender.source),
            dim=contender.dim,
            model_file=contender.model_file,
            size_in_gb=contender.declared_gb,
        )
