"""評価セットをファイルから読む。

リポジトリに置くものは架空の会話で書く。実際の会話から作ったものは手元に置く。
読むだけで判断しない。確認前の件を断るのは測る側である。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import final

from yadori.domain.evaluation import CannotMeasure, Case, Exchange, RecallEval


@final
class EvalFile:
    def __init__(self, path: Path) -> None:
        self._path: Path = path

    def read(self) -> RecallEval:
        """評価セットを読む。

        - 書いてあるものを取り出す
        - 覚えさせるやりとりを組む
        - 測る件を組む
        """
        written = self._written()
        return RecallEval(
            within=self._within(written),
            exchanges=self._exchanges(written),
            cases=self._cases(written),
        )

    def _written(self) -> dict[str, object]:
        if not self._path.exists():
            raise CannotMeasure(f"{self._path} がありません")
        return tomllib.loads(self._path.read_text(encoding="utf-8"))

    def _within(self, written: dict[str, object]) -> int:
        value = written.get("within")
        if not isinstance(value, int):
            raise CannotMeasure("within（何位までを満たしたとするか）がありません")
        return value

    def _exchanges(self, written: dict[str, object]) -> tuple[Exchange, ...]:
        return tuple(
            Exchange(
                name=self._text(row, "name"),
                utterance=self._text(row, "utterance"),
                reply=self._text(row, "reply", allow_empty=True),
            )
            for row in self._rows(written, "exchange")
        )

    def _cases(self, written: dict[str, object]) -> tuple[Case, ...]:
        return tuple(
            Case(
                name=self._text(row, "name"),
                utterance=self._text(row, "utterance"),
                expected=self._names(row, "expected"),
                forbidden=self._names(row, "forbidden"),
                confirmed=self._confirmed(row),
            )
            for row in self._rows(written, "case")
        )

    def _confirmed(self, row: dict[str, object]) -> bool | None:
        """確認済みの印。欄が無ければ人が書いた件であり、判断は測る側が持つ。"""
        value = row.get("confirmed")
        if value is None:
            return None
        if not isinstance(value, bool):
            raise CannotMeasure("confirmed は true か false で書いてください")
        return value

    def _rows(self, written: dict[str, object], key: str) -> list[dict[str, object]]:
        rows = written.get(key)
        if not isinstance(rows, list) or not rows:
            raise CannotMeasure(f"{key} がありません")
        found: list[dict[str, object]] = []
        for row in rows:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(row, dict):
                raise CannotMeasure(f"{key} の書き方が違います")
            found.append(row)  # pyright: ignore[reportUnknownArgumentType]
        return found

    def _text(self, row: dict[str, object], key: str, *, allow_empty: bool = False) -> str:
        """文字の欄。返事は記録に残っていないことがあるため、空を許す。"""
        value = row.get(key)
        if not isinstance(value, str) or (not allow_empty and not value.strip()):
            raise CannotMeasure(f"{key} が文字で書かれていません")
        return value

    def _names(self, row: dict[str, object], key: str) -> tuple[str, ...]:
        value = row.get(key, [])
        if not isinstance(value, list):
            raise CannotMeasure(f"{key} は名前の並びで書いてください")
        found: list[str] = []
        for name in value:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(name, str):
                raise CannotMeasure(f"{key} に文字でないものがあります")
            found.append(name)
        return tuple(found)
