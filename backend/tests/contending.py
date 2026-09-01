"""INC-031 のテストで使う差し替え。

AIモデルを読み込まずに、名前の解き方と重さと添え書きを確かめる。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import final

from yadori.adapter.embedding import CharacterPairs, Prepared, Size
from yadori.domain.memory import Prefixes, Provenance, Vector

PAIRS = CharacterPairs()
RURI = Prefixes(remember="検索文書: ", recall="検索クエリ: ")


@final
class Runner:
    """道具の代わり。渡った文字列を記録し、語の重なりの並びを返す。"""

    def __init__(self) -> None:
        self.received: list[str] = []

    def embed(self, documents: list[str]) -> Iterable[Iterable[float]]:
        self.received.extend(documents)
        return [PAIRS.to_remember(one) for one in documents]


@final
class Recording:
    """側ごとに受け取った文を記録する埋め込み。"""

    def __init__(self) -> None:
        self.remembered: list[str] = []
        self.recalled: list[str] = []

    @property
    def provenance(self) -> Provenance:
        return Provenance(None, "recording", "v1")

    @property
    def name(self) -> str:
        return self.provenance.index_name

    def to_remember(self, text: str) -> Vector:
        self.remembered.append(text)
        return PAIRS.to_remember(text)

    def to_recall(self, text: str) -> Vector:
        self.recalled.append(text)
        return PAIRS.to_recall(text)


@final
class Heavy:
    """支度の口を持ち、決まった読み込みの時間と大きさを答える埋め込み。"""

    def __init__(
        self,
        loaded_in: float = 1.5,
        size: Size | None = None,
        prefixes: Prefixes | None = None,
        ai_model: str = "heavy",
    ) -> None:
        self._loaded_in: float = loaded_in
        self._size: Size = size or Size.of(150_000_000)
        self._prefixes: Prefixes | None = prefixes
        self._ai_model: str = ai_model
        self.prepared: int = 0

    @property
    def provenance(self) -> Provenance:
        return Provenance(self._ai_model, "fake-tool", "v1", self._prefixes)

    @property
    def name(self) -> str:
        return self.provenance.index_name

    def prepare(self) -> Prepared:
        self.prepared += 1
        return Prepared(loaded_in=self._loaded_in, size=self._size)

    def to_remember(self, text: str) -> Vector:
        return PAIRS.to_remember(text)

    def to_recall(self, text: str) -> Vector:
        return PAIRS.to_recall(text)


@final
class Growing:
    """呼ぶたびに歩幅が伸びる時計。一発話の時間が測るたびに違う値になる。"""

    def __init__(self) -> None:
        self._at: float = 0.0
        self._calls: int = 0

    def __call__(self) -> float:
        self._calls += 1
        self._at += 0.001 * self._calls
        return self._at


@final
class Stepping:
    """決めた値を順に返す時計。"""

    def __init__(self, values: list[float]) -> None:
        self._values: list[float] = list(values)

    def __call__(self) -> float:
        return self._values.pop(0)
