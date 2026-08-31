"""測ることと下書きを作ることが外へ求めること。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from yadori.domain.evaluation.model import Pair, RecallEval, Recorded


class Records(Protocol):
    """対話する道具の記録の読み手。形式ごとに一つ。

    自分の形式かをファイルの先頭で答え、読めるなら記録の一往復の並びを返す。
    形式に依る雑音（道具の命令、貼り付け、画像だけの発話）はここで除く。
    中身の無さの規則は知らない。
    """

    def claims(self, path: Path) -> bool: ...

    def read(self, path: Path) -> tuple[Recorded, ...]: ...


class Judge(Protocol):
    """判定。一つの作業場所の発話の並びを受け取り、後の発話が前のどの発話の
    話題を指すかを組で返す。

    受け取るのは発話の文章だけである。返事、時刻、作業場所は渡らない
    （ADR-017）。続けられなければ `CannotDraft` を投げる。
    """

    def pairs(self, utterances: Sequence[str]) -> tuple[Pair, ...]: ...


class DraftWriter(Protocol):
    """下書きの書き手。出力先の境界を守り、書けなければ `CannotDraft` を投げる。"""

    def write(self, path: Path, recall_eval: RecallEval) -> None: ...
