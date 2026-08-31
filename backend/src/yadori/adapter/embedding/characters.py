"""文字の並びから作る埋め込み。

外のAIモデルを呼ばずに動くため、鍵が無い環境でも記憶の規則を確かめられる。
語の重なりしか見ないため、言い換えの近さは捉えられない。桁数が有限なので
別の組が同じ桁へ入ることがあり、短い文章ほどその影響が大きい。応対の質を
求める場面ではAIモデルを使う実装へ差し替える。インデックスは原文から作り直せるので、
差し替えは後からできる。
"""

from __future__ import annotations

import math
import zlib
from collections import Counter
from typing import final

from yadori.domain.memory import Vector

_DIMENSIONS = 512


@final
class CharacterPairs:
    @property
    def name(self) -> str:
        return "character-pairs-v1"

    def of(self, text: str) -> Vector:
        counts: Counter[int] = Counter()
        stripped = "".join(text.split())
        for index in range(max(len(stripped) - 1, 0)):
            counts[self._bucket(stripped[index : index + 2])] += 1
        if not counts:
            return tuple(0.0 for _ in range(_DIMENSIONS))

        length = math.sqrt(sum(count * count for count in counts.values()))
        return tuple(counts.get(index, 0) / length for index in range(_DIMENSIONS))

    def _bucket(self, pair: str) -> int:
        """文字の組を桁へ割り当てる。

        組み込みの `hash` はプロセスごとに値が変わるため使わない。保存した
        インデックスを次の起動で読めなくなり、原文から作り直せるという前提が崩れる。
        """
        return zlib.crc32(pair.encode("utf-8")) % _DIMENSIONS


@final
class Closeness:
    """二つの並びがどれくらい近いか。

    長さで割ってから比べる。長さ1で返す埋め込みを前提にすると、そうでない
    実装へ替えたときに一を超える値が出て、下限がどこでも効かなくなる。前提を
    置かないほうが、埋め込みを差し替えられるという設計に合う。
    """

    def between(self, left: Vector, right: Vector) -> float:
        lengths = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
        if lengths == 0:
            return 0.0
        return sum(a * b for a, b in zip(left, right, strict=True)) / lengths
