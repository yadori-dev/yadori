"""記録から評価セットの下書きを作って、結果の数を書く。

数は手順が返し、書くのと画面へ出すのはここが持つ。下書きの失敗は理由を標準
エラーへ書いて 1 を返す。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO, final

from yadori.adapter.evaluation import ClaudeCodeJudge, ClaudeCodeRecords, CodexRecords, DraftFile
from yadori.adapter.tool import ClaudeCodeCall
from yadori.domain.evaluation import CannotDraft, Judge
from yadori.infrastructure.settings import DEFAULT_MODEL
from yadori.usecase.evaluation import Drafting

# 判定は一つの作業場所の発話を全部一度に渡すため、応対より長く待つ。
JUDGE_WAIT_SECONDS = 900


@final
class Drafter:
    """記録の置き場を指して、下書きを作る。"""

    def __init__(
        self,
        places: Sequence[Path],
        out: Path,
        judge: Judge | None = None,
        writing: TextIO | None = None,
    ) -> None:
        self._places: tuple[Path, ...] = tuple(places)
        self._out: Path = out
        self._judge: Judge = judge or ClaudeCodeJudge(
            ClaudeCodeCall(DEFAULT_MODEL, JUDGE_WAIT_SECONDS)
        )
        self._writing: TextIO = writing or sys.stdout

    def run(self) -> int:
        """作って、数を書く。"""
        drafting = Drafting([ClaudeCodeRecords(), CodexRecords()], self._judge, DraftFile())
        try:
            draft = drafting.run(self._places, self._out)
        except CannotDraft as reason:
            print(f"下書きを作れません: {reason}", file=sys.stderr)
            return 1
        self._say(
            f"記録: {draft.sessions} セッション、中身のある発話 {draft.spoken} 件"
            + f"（読めず飛ばしたファイル {draft.skipped_files}）"
        )
        self._say(f"覚えさせる発話: {draft.exchanges} 件")
        self._say(f"件: {draft.cases} 件（後の発話が前の話題を指していそうなもの。すべて確認前）")
        self._say(f"  {self._out} の各件を読み、残す件は confirmed = true にしてください")
        return 0

    def _say(self, line: str) -> None:
        _ = self._writing.write(f"{line}\n")
