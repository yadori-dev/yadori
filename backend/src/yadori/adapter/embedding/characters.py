"""文字の並びから作る埋め込み。

外の模型を呼ばずに動くため、鍵が無い環境でも記憶の規則を確かめられる。
語の重なりしか見ないため、言い換えの近さは捉えられない。桁数が有限なので
別の組が同じ桁へ入ることがあり、短い文章ほどその影響が大きい。応対の質を
求める場面では模型を使う実装へ差し替える。索引は原文から作り直せるので、
差し替えは後からできる。
"""

from __future__ import annotations

import math
import zlib
from collections import Counter

from yadori.domain.memory import Vector

_DIMENSIONS = 512


def _bucket(pair: str) -> int:
    """文字の組を桁へ割り当てる。

    組み込みの `hash` はプロセスごとに値が変わるため使わない。保存した索引を
    次の起動で読めなくなり、原文から作り直せるという前提が崩れる。
    """
    return zlib.crc32(pair.encode("utf-8")) % _DIMENSIONS


class CharacterPairs:
    @property
    def name(self) -> str:
        return "character-pairs-v1"

    def of(self, text: str) -> Vector:
        counts: Counter[int] = Counter()
        stripped = "".join(text.split())
        for index in range(max(len(stripped) - 1, 0)):
            counts[_bucket(stripped[index : index + 2])] += 1
        if not counts:
            return tuple(0.0 for _ in range(_DIMENSIONS))

        length = math.sqrt(sum(count * count for count in counts.values()))
        return tuple(counts.get(index, 0) / length for index in range(_DIMENSIONS))


def closeness(left: Vector, right: Vector) -> float:
    """二つの並びがどれくらい近いか。どちらも長さ1のため内積でよい。"""
    return sum(a * b for a, b in zip(left, right, strict=True))
