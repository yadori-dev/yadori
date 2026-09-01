"""重さを量る包みと、支度の口。

どの埋め込みも包め、側ごとの呼び出しを包んだ先へそのまま渡しながら一発話の時間を
足し上げる。包んだ先が支度の口を持てば、最初の呼び出しの前に一度だけ支度を呼んで
読み込みの時間と大きさを受け取る。重さを読むのは測る入口だけなので、domain の
埋め込みの口には足さず、ここで量る。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, final, runtime_checkable

from yadori.domain.memory import Embeddings, Provenance, Vector


@dataclass(frozen=True)
class Size:
    """取得した物の大きさ。値か、無し（取得する物が無い）か、不明（置き場が分からない）。"""

    bytes: int | None
    known: bool

    @classmethod
    def of(cls, bytes: int) -> Size:
        return cls(bytes=bytes, known=True)

    @classmethod
    def absent(cls) -> Size:
        return cls(bytes=None, known=True)

    @classmethod
    def unknown(cls) -> Size:
        return cls(bytes=None, known=False)

    @property
    def described(self) -> str:
        if self.bytes is not None:
            return f"{self.bytes / 1e9:.2f}GB"
        return "無し" if self.known else "不明"


@dataclass(frozen=True)
class Prepared:
    """支度の結果。読み込みにかかった時間（秒）と、取得した物の大きさ。"""

    loaded_in: float
    size: Size


@runtime_checkable
class Preparing(Protocol):
    """支度の口。AIモデルを読み込める状態にし、読み込みの時間と大きさを答える。"""

    def prepare(self) -> Prepared: ...


@dataclass(frozen=True)
class Weight:
    """重さ。取得した物の大きさ、読み込みにかかった時間、一発話にかかった時間の平均。

    時間は秒。支度の口が無ければ読み込みは無し。区切りの間に一度も呼ばれていなければ
    一発話は無し。
    """

    size: Size
    loaded_in: float | None
    per_text: float | None

    @property
    def described(self) -> str:
        return (
            f"大きさ {self.size.described} / 読み込み {self._seconds(self.loaded_in)}"
            + f" / 一発話 {self._seconds(self.per_text)}"
        )

    def _seconds(self, value: float | None) -> str:
        return "無し" if value is None else f"{value:.3f}秒"


@final
class Weighing:
    """重さを量る包み。出自と名前は包んだ先のものをそのまま返す。"""

    def __init__(self, inner: Embeddings, clock: Callable[[], float] = time.perf_counter) -> None:
        self._inner: Embeddings = inner
        self._clock: Callable[[], float] = clock
        self._prepared: Prepared | None = None
        self._readied: bool = False
        self._spent: float = 0.0
        self._count: int = 0

    @property
    def provenance(self) -> Provenance:
        return self._inner.provenance

    @property
    def name(self) -> str:
        return self._inner.name

    def to_remember(self, text: str) -> Vector:
        return self._timed(lambda: self._inner.to_remember(text))

    def to_recall(self, text: str) -> Vector:
        return self._timed(lambda: self._inner.to_recall(text))

    def weighed(self) -> Weight:
        """量って区切る。一発話の足し上げはここでやり直す。読み込みと大きさは残る。

        性質として読める名前にしないのは、読み取りに副作用を隠さないためである。
        """
        per_text = None if self._count == 0 else self._spent / self._count
        self._spent, self._count = 0.0, 0
        if self._prepared is None:
            return Weight(size=Size.absent(), loaded_in=None, per_text=per_text)
        return Weight(
            size=self._prepared.size, loaded_in=self._prepared.loaded_in, per_text=per_text
        )

    def _timed(self, making: Callable[[], Vector]) -> Vector:
        self._ready()
        started = self._clock()
        made = making()
        self._spent += self._clock() - started
        self._count += 1
        return made

    def _ready(self) -> None:
        """支度の口があれば、最初の呼び出しの前に一度だけ呼ぶ。支度は一発話の時間に含めない。"""
        if self._readied:
            return
        self._readied = True
        if isinstance(self._inner, Preparing):
            self._prepared = self._inner.prepare()
