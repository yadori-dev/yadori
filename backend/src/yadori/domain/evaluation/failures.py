"""測ることと下書きを作ることが失敗したときの理由。"""

from __future__ import annotations


class CannotMeasure(Exception):
    """測れる形になっていない。

    一件も測らずに理由を返す。一部だけ測った結果は、順位が変わった理由を
    条件の変更と取り違えさせる。
    """


class CannotDraft(Exception):
    """下書きを作れない。

    記録が読めない、判定が続かない、出力先が境界の外にある。何も書かずに
    理由を返す。中途半端な下書きを残さない。
    """


class BrokenRecord(Exception):
    """一つの記録のファイルが、形式は合っているが途中で読めない。

    そのファイルだけを飛ばし、数える。下書き全体は続ける。
    """
