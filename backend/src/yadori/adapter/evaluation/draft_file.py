"""下書きを評価セットのファイルとして書く。

出力先の境界を守る。リポジトリの配下、既にあるファイル、ディレクトリには書かず、
下書きの失敗として断る。人の確認を消さないためと、会話の原文をリポジトリへ
入れないためである。発話と返事は記録の原文をそのまま書き、言い換えない。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import final

from yadori.domain.evaluation import CannotDraft, Case, Exchange, RecallEval

HEADING = (
    "# 実際の会話の記録から作った評価セットの下書き。手元に置き、リポジトリへ入れない。\n"
    "# 各件を読み、期待が妥当なら confirmed = true にし、違えば件を消す。\n"
    "# overlap は件と期待の語の重なりの度合いで、小さいほど言い換えの件である。\n"
)


@final
class DraftFile:
    def write(self, path: Path, recall_eval: RecallEval) -> None:
        self._refuse_outside(path)
        _ = path.write_text(self._text(recall_eval), encoding="utf-8")

    def _refuse_outside(self, path: Path) -> None:
        if path.is_dir():
            raise CannotDraft(f"出力先 {path} はディレクトリです。ファイルを指してください")
        if path.exists():
            raise CannotDraft(f"出力先 {path} は既にあります。人の確認を消さないため上書きしません")
        if any((parent / ".git").exists() for parent in path.resolve().parents):
            raise CannotDraft(
                f"出力先 {path} はリポジトリの配下です。会話の原文はリポジトリへ入れません"
            )
        if not path.parent.is_dir():
            raise CannotDraft(f"出力先の置き場 {path.parent} がありません")

    def _text(self, recall_eval: RecallEval) -> str:
        lines = [HEADING, f"within = {recall_eval.within}", ""]
        for exchange in recall_eval.exchanges:
            lines.extend(self._exchange(exchange))
        for case in recall_eval.cases:
            lines.extend(self._case(case))
        return "\n".join(lines)

    def _exchange(self, exchange: Exchange) -> list[str]:
        return [
            "[[exchange]]",
            f"name = {self._quoted(exchange.name)}",
            f"utterance = {self._quoted(exchange.utterance)}",
            f"reply = {self._quoted(exchange.reply)}",
            "",
        ]

    def _case(self, case: Case) -> list[str]:
        overlap = ", ".join(f"{name} = {value}" for name, value in case.overlap)
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
        # JSON の文字列の書き方は TOML の基本文字列としてそのまま読める。
        return json.dumps(text, ensure_ascii=False)
