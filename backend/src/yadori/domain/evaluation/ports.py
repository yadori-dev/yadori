"""測ることと下書きを作ることが外へ求めること。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from yadori.domain.evaluation.model import Asking, Pair, RecallEval, Recorded


class Records(Protocol):
    """対話する道具の記録の読み手。形式ごとに一つ。

    自分の形式かをファイルの先頭で答え、読めるなら記録の一往復の並びを返す。
    形式に依る雑音（道具の命令、貼り付け、画像だけの発話）はここで除く。
    中身の無さの規則は知らない。
    """

    def claims(self, path: Path) -> bool: ...

    def read(self, path: Path) -> tuple[Recorded, ...]: ...


class Judge(Protocol):
    """判定。後の発話と候補の問いをいくつか受け取り、候補のうちどれが同じ話題かを
    組で返す。

    受け取るのは発話と候補の文章だけである。返事、時刻、作業場所は渡らない
    （ADR-017）。作業場所の発話を丸ごと受け取ることはない。続けられなければ
    `CannotDraft` を投げる。
    """

    def pairs(self, askings: Sequence[Asking]) -> tuple[Pair, ...]: ...


class DraftWriter(Protocol):
    """下書きの書き手。出力先の境界を守り、書けなければ `CannotDraft` を投げる。

    何で候補を引いたか（埋め込みの名前と条件）を冒頭に残す。後から比べるために要る。
    """

    def write(self, path: Path, recall_eval: RecallEval, drawn_with: str) -> None: ...
