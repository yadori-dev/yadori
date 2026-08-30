"""測ることが外へ求めること。"""

from __future__ import annotations


class CannotMeasure(Exception):
    """測れる形になっていない。

    一件も測らずに理由を返す。一部だけ測った結果は、順位が変わった理由を
    条件の変更と取り違えさせる。
    """
