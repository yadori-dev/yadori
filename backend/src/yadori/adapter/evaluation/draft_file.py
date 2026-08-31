"""下書きを評価セットのファイルとして書き、読み、追記する。下書きの置き場の実装。

出力先の境界を守る。リポジトリの配下、ディレクトリには書かず、新しく作るときは
既にあるファイルにも書かず、下書きの失敗として断る。人の確認を消さないためと、
会話の原文をリポジトリへ入れないためである。発話と返事は記録の原文をそのまま書き、
言い換えない。

前回の範囲は `[covered]` の表として冒頭に置く。追記は文字の並びのまま末尾へ足し、
`[covered]` だけを置き換え、それ以外は一字も変えない。値に読んで書き戻すと、人が
書いた注釈、並び、書き方が消える。
"""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import final

from yadori.adapter.evaluation.file import EvalFile
from yadori.domain.evaluation import (
    Added,
    CannotDraft,
    CannotMeasure,
    Case,
    Covered,
    DrawnWith,
    Exchange,
    RecallEval,
)
from yadori.domain.memory import HowToRecall, Provenance

HEADING = (
    "# 実際の会話の記録から作った評価セットの下書き。手元に置き、リポジトリへ入れない。\n"
    "# 各問を読み、期待が妥当なら confirmed = true にし、違えば問を消す。\n"
    "# overlap は問と期待の語の重なりの度合いで、小さいほど言い換えの問である。\n"
    "# 候補は宿りの思い出す仕組みで引いたものだけ。拾えなかった組（下限を下回る、件数の上限から\n"
    "# 外れた）は手で足せる。直近の範囲の組は測れないので足さない。\n"
)
COVERED_NOTE = (
    "# 道具が書く前回の範囲。追記（--append）はここから読む。人は変えない。\n"
    "# この表と次の見出しの間に書いた行と、この見出しの行に書いた注釈は追記で消える。\n"
)
COVERED_HEAD = "[covered]"
NOT_APPENDABLE = "前回の範囲を持たないので追記できません。新しい版の道具で作り直してください"


@final
class DraftFile:
    def write(self, path: Path, recall_eval: RecallEval, covered: Covered) -> None:
        self._refuse_outside(path)
        if path.exists():
            raise CannotDraft(f"出力先 {path} は既にあります。人の確認を消さないため上書きしません")
        if not path.parent.is_dir():
            raise CannotDraft(f"出力先のディレクトリ {path.parent} がありません")
        lines = [
            HEADING,
            f"within = {recall_eval.within}",
            "",
            COVERED_NOTE + self._covered(covered),
            "",
        ]
        lines.extend(self._blocks(recall_eval.exchanges, recall_eval.cases))
        _ = path.write_text("\n".join(lines), encoding="utf-8")

    def read(self, path: Path) -> tuple[RecallEval, Covered]:
        """前回の下書きを読む。前回の範囲を持たないものは追記の対象でない。

        出力先の境界はここでも見る。判定を全部走らせた後に断るのは遅い。
        """
        self._refuse_missing(path)
        self._refuse_outside(path)
        text = path.read_text(encoding="utf-8")
        try:
            written = tomllib.loads(text)
        except tomllib.TOMLDecodeError as broken:
            raise CannotDraft(f"{path} を評価セットとして読めません: {broken}") from broken
        table = written.get("covered")
        if not isinstance(table, dict):
            raise CannotDraft(f"{path} は{NOT_APPENDABLE}")
        # 追記で置き換える見出しの行も、判定を呼ぶ前にここで見つけておく。
        _ = self._around_covered(path, text)
        try:
            recall_eval = EvalFile(path).read()
        except CannotMeasure as broken:
            raise CannotDraft(f"{path} を評価セットとして読めません: {broken}") from broken
        return recall_eval, _CoveredTable(path, table).covered()  # pyright: ignore[reportUnknownArgumentType]

    def append(self, path: Path, added: Added, covered: Covered) -> None:
        """前回の分を一字も変えずに、前回の範囲を置き換え、足す分を末尾に足す。

        同じディレクトリに一時ファイルを書いてから差し替えるので、途中で落ちても元は残る。
        差し替えた結果に前回のやりとりと問の名前がすべて残っていることを、書く前に確かめる。
        切り方を誤って前回の分を消すのは、失敗を名乗らずに人の確認を失う最悪の形だからである。
        """
        self._refuse_missing(path)
        self._refuse_outside(path)
        text = path.read_text(encoding="utf-8")
        head, tail = self._around_covered(path, text)
        if tail and not tail.endswith("\n"):
            tail += "\n"
        blocks = self._blocks(added.exchanges, added.cases)
        joined = (
            head
            + self._covered(covered)
            + "\n"
            + tail
            + ("\n" if blocks else "")
            + "\n".join(blocks)
        )
        self._verify_kept(path, text, joined, added)
        self._replace(path, joined)

    def _verify_kept(self, path: Path, before: str, after: str, added: Added) -> None:
        """前回の名前がすべて残り、足した分だけ増えていることを、文字の並びから読み直して確かめる。"""
        was = self._names_in(path, before)
        now = self._names_in(path, after)
        expected = (
            was
            | {("exchange", one.name) for one in added.exchanges}
            | {("case", one.name) for one in added.cases}
        )
        if now != expected:
            raise CannotDraft(
                f"{path} への追記で前回の分を保てないので書きませんでした。道具の側の不具合です。"
                + "下書きはそのままで、作り直す必要はありません"
            )

    def _names_in(self, path: Path, text: str) -> set[tuple[str, str]]:
        try:
            written: dict[str, object] = tomllib.loads(text)
        except tomllib.TOMLDecodeError as broken:
            raise CannotDraft(f"{path} を評価セットとして読めません: {broken}") from broken
        found: set[tuple[str, str]] = set()
        for key in ("exchange", "case"):
            rows = written.get(key, [])
            if not isinstance(rows, list):
                raise CannotDraft(f"{path} の {key} の書き方が違います")
            for row in rows:  # pyright: ignore[reportUnknownVariableType]
                if isinstance(row, dict) and isinstance(row.get("name"), str):  # pyright: ignore[reportUnknownMemberType]
                    found.add((key, row["name"]))  # pyright: ignore[reportUnknownArgumentType]
        return found

    def _refuse_missing(self, path: Path) -> None:
        if path.is_dir():
            raise CannotDraft(f"出力先 {path} はディレクトリです。ファイルを指してください")
        if not path.exists():
            raise CannotDraft(f"下書き {path} がありません。追記は既にある下書きにだけできます")

    def _refuse_outside(self, path: Path) -> None:
        if path.is_dir():
            raise CannotDraft(f"出力先 {path} はディレクトリです。ファイルを指してください")
        if any((parent / ".git").exists() for parent in path.resolve().parents):
            raise CannotDraft(
                f"出力先 {path} はリポジトリの配下です。会話の原文はリポジトリへ入れません"
            )

    def _around_covered(self, path: Path, text: str) -> tuple[str, str]:
        """`[covered]` の前と、その次の見出し（無ければ末尾）から後。表の中身は返さない。"""
        lines = text.splitlines(keepends=True)
        start = next(
            # 見出しの後ろには空白か注釈しか来ない。
            (index for index, line in enumerate(lines) if line.strip().startswith(COVERED_HEAD)),
            None,
        )
        if start is None:
            raise CannotDraft(f"{path} は{NOT_APPENDABLE}")
        end = next(
            (
                index
                for index in range(start + 1, len(lines))
                if lines[index].lstrip().startswith("[")
            ),
            len(lines),
        )
        return "".join(lines[:start]), "".join(lines[end:])

    def _replace(self, path: Path, text: str) -> None:
        handle, temporary = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as writing:
                _ = writing.write(text)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def _covered(self, covered: Covered) -> str:
        drawn = covered.drawn_with
        pieces = (
            []
            if drawn.provenance.ai_model is None
            else [f"ai_model = {self._quoted(drawn.provenance.ai_model)}"]
        )
        pieces.extend(
            [
                f"tool = {self._quoted(drawn.provenance.tool)}",
                f"tool_version = {self._quoted(drawn.provenance.tool_version)}",
                f"recent = {drawn.how.recent_turns}",
                f"limit = {drawn.how.found_limit}",
                f"floor = {drawn.how.relevance_floor}",
                f"judge = {self._quoted(drawn.judge)}",
            ]
        )
        return "\n".join(
            [
                COVERED_HEAD,
                f"until = {covered.until.isoformat()}",
                f"places = [{', '.join(self._quoted(place) for place in covered.places)}]",
                f"skipped = [{', '.join(self._quoted(skipped) for skipped in covered.skipped)}]",
                f"sessions = {covered.sessions}",
                f"last_exchange = {covered.last_exchange}",
                f"last_case = {covered.last_case}",
                f"drawn_with = {{ {', '.join(pieces)} }}",
                "",
            ]
        )

    def _blocks(self, exchanges: Sequence[Exchange], cases: Sequence[Case]) -> list[str]:
        lines: list[str] = []
        for exchange in exchanges:
            lines.extend(self._exchange(exchange))
        for case in cases:
            lines.extend(self._case(case))
        return lines

    def _exchange(self, exchange: Exchange) -> list[str]:
        return [
            "[[exchange]]",
            f"name = {self._quoted(exchange.name)}",
            f"utterance = {self._quoted(exchange.utterance)}",
            f"reply = {self._quoted(exchange.reply)}",
            "",
        ]

    def _case(self, case: Case) -> list[str]:
        # 見出しは引用して書く。人が変えたやりとりの名前は裸の見出しで書けない文字を持ち得る。
        overlap = ", ".join(f"{self._quoted(name)} = {value}" for name, value in case.overlap)
        return [
            "[[case]]",
            f"name = {self._quoted(case.name)}",
            f"utterance = {self._quoted(case.utterance)}",
            f"expected = [{', '.join(self._quoted(name) for name in case.expected)}]",
            f"forbidden = [{', '.join(self._quoted(name) for name in case.forbidden)}]",
            f"confirmed = {'true' if case.confirmed else 'false'}",
            f"overlap = {{ {overlap} }}",
            "",
        ]

    def _quoted(self, text: str) -> str:
        # JSON の文字列の書き方は TOML の基本文字列としてそのまま読める。ただし DEL（U+007F）
        # だけは JSON が素通しし TOML が禁じるため、逃がす。
        return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


@final
class _CoveredTable:
    """`[covered]` の表を、型を確かめながら値にする。欠けは同じ系列の文面で断る。"""

    def __init__(self, path: Path, table: dict[str, object]) -> None:
        self._path: Path = path
        self._table: dict[str, object] = table

    def covered(self) -> Covered:
        drawn = self._table.get("drawn_with")
        if not isinstance(drawn, dict):
            raise self._broken("drawn_with")
        return Covered(
            until=self._datetime("until"),
            places=self._names(self._table, "places"),
            skipped=self._names(self._table, "skipped"),
            sessions=self._int(self._table, "sessions"),
            last_exchange=self._int(self._table, "last_exchange"),
            last_case=self._int(self._table, "last_case"),
            drawn_with=self._drawn_with(drawn),  # pyright: ignore[reportUnknownArgumentType]
        )

    def _drawn_with(self, drawn: dict[str, object]) -> DrawnWith:
        ai_model = drawn.get("ai_model")
        if ai_model is not None and not isinstance(ai_model, str):
            raise self._broken("drawn_with.ai_model")
        return DrawnWith(
            provenance=Provenance(
                ai_model=ai_model,
                tool=self._text(drawn, "tool"),
                tool_version=self._text(drawn, "tool_version"),
            ),
            how=HowToRecall(
                recent_turns=self._int(drawn, "recent"),
                found_limit=self._int(drawn, "limit"),
                relevance_floor=self._float(drawn, "floor"),
            ),
            judge=self._text(drawn, "judge"),
        )

    def _datetime(self, key: str) -> datetime:
        value = self._table.get(key)
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise self._broken(key)
        return value

    def _names(self, table: dict[str, object], key: str) -> tuple[str, ...]:
        value = table.get(key)
        if not isinstance(value, list):
            raise self._broken(key)
        found: list[str] = []
        for name in value:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(name, str):
                raise self._broken(key)
            found.append(name)
        return tuple(found)

    def _int(self, table: dict[str, object], key: str) -> int:
        value = table.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise self._broken(key)
        return value

    def _float(self, table: dict[str, object], key: str) -> float:
        value = table.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise self._broken(key)
        return float(value)

    def _text(self, table: dict[str, object], key: str) -> str:
        value = table.get(key)
        if not isinstance(value, str):
            raise self._broken(key)
        return value

    def _broken(self, key: str) -> CannotDraft:
        return CannotDraft(
            f"{self._path} の前回の範囲に {key} が無いか読めないので追記できません。"
            + "新しい版の道具で作り直してください"
        )
