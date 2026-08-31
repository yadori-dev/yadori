"""記録から評価セットの下書きを作って、結果の数を書く。

数は手順が返し、書くのと画面へ出すのはここが持つ。下書きの失敗と、埋め込みが
使えない失敗は、理由を標準エラーへ書いて 1 を返す。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO, final

from yadori.adapter.embedding import Multilingual
from yadori.adapter.evaluation import ClaudeCodeJudge, ClaudeCodeRecords, CodexRecords, DraftFile
from yadori.adapter.store import InMemoryMemories
from yadori.adapter.tool import ClaudeCodeCall
from yadori.domain.evaluation import CannotDraft, Judge
from yadori.domain.memory import Embeddings, EmbeddingsUnavailable, HowToRecall
from yadori.infrastructure.settings import DEFAULT_MODEL, SettingsFile
from yadori.usecase.evaluation import DRAFT_HOW, Drafting

# 判定は問いを十ずつ渡すので小さいが、回数が多い。応対と同じ待ち時間で足りる。
JUDGE_WAIT_SECONDS = 180


@final
class Drafter:
    """記録のディレクトリを指して、下書きを作る。"""

    def __init__(
        self,
        places: Sequence[Path],
        out: Path,
        judge: Judge | None = None,
        embeddings: Embeddings | None = None,
        how: HowToRecall | None = None,
        writing: TextIO | None = None,
    ) -> None:
        self._places: tuple[Path, ...] = tuple(places)
        self._out: Path = out
        # 宿りの設定（dweller.toml）が無くても下書きは作れるように、既定のAIモデルで呼ぶ。
        self._judge: Judge = judge or ClaudeCodeJudge(
            ClaudeCodeCall(DEFAULT_MODEL, JUDGE_WAIT_SECONDS)
        )
        self._embeddings: Embeddings = embeddings or Multilingual(
            cache_dir=SettingsFile().models_path
        )
        self._how: HowToRecall = how or DRAFT_HOW
        self._writing: TextIO = writing or sys.stdout

    def run(self) -> int:
        """作って、数を書く。"""
        drafting = Drafting(
            [ClaudeCodeRecords(), CodexRecords()],
            self._judge,
            DraftFile(),
            self._embeddings,
            InMemoryMemories,
            self._how,
        )
        try:
            draft = drafting.run(self._places, self._out)
        except (CannotDraft, EmbeddingsUnavailable) as reason:
            print(f"下書きを作れません: {reason}", file=sys.stderr)
            return 1
        self._say(f"候補を引いた {drafting.drawn_with()}")
        self._say(
            f"記録: {draft.sessions} セッション、中身のある発話 {draft.spoken} 件"
            + f"（読めず飛ばしたファイル {draft.skipped_files}）"
        )
        self._say(f"覚えさせる発話: {draft.exchanges} 件")
        self._say(f"件: {draft.cases} 件（後の発話が前の話題を指していそうなもの。すべて確認前）")
        self._say(f"  {self._out} の各件を読み、残す件は confirmed = true にしてください")
        self._say(
            "  候補は思い出す仕組みが引いたものだけです。拾えなかった組（下限を下回る、件数の"
            + "上限から外れた）は手で足せます。直近の範囲の組は測れないので足しません"
        )
        return 0

    def _say(self, line: str) -> None:
        _ = self._writing.write(f"{line}\n")
