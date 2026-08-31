"""記録から評価セットの下書きを作る、または既にある下書きへ増えた分を足して、結果の数を書く。

数は手順が返し、書くのと画面へ出すのはここが持つ。下書きの失敗と、埋め込みが
使えない失敗と、下書きを読み書きできない失敗（権限、容量）は、理由を標準エラーへ
書いて 1 を返す。
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
from yadori.domain.evaluation import Appended, CannotDraft, Draft, Judge
from yadori.domain.memory import Embeddings, EmbeddingsUnavailable, HowToRecall
from yadori.infrastructure.settings import DEFAULT_MODEL, SettingsFile
from yadori.usecase.evaluation import DRAFT_HOW, Drafting

# 判定は問いを十ずつ渡すので小さいが、回数が多い。応対と同じ待ち時間で足りる。
JUDGE_WAIT_SECONDS = 180
NOT_CAUGHT = (
    "  候補は思い出す仕組みが引いたものだけです。拾えなかった組（下限を下回る、件数の"
    + "上限から外れた）は手で足せます。直近の範囲の組は測れないので足しません"
)


@final
class Drafter:
    """記録のディレクトリを指して、下書きを作るか、既にある下書きへ足す。"""

    def __init__(
        self,
        places: Sequence[Path],
        out: Path,
        append: bool = False,
        judge: Judge | None = None,
        embeddings: Embeddings | None = None,
        how: HowToRecall | None = None,
        writing: TextIO | None = None,
    ) -> None:
        self._places: tuple[Path, ...] = tuple(places)
        self._out: Path = out
        self._append: bool = append
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
        """作るか足して、数を書く。"""
        drafting = Drafting(
            [ClaudeCodeRecords(), CodexRecords()],
            self._judge,
            DraftFile(),
            self._embeddings,
            InMemoryMemories,
            self._how,
        )
        try:
            if self._append:
                self._appended(drafting.append(self._places, self._out), drafting)
            else:
                self._drafted(drafting.run(self._places, self._out), drafting)
        except (CannotDraft, EmbeddingsUnavailable) as reason:
            print(f"下書きを作れません: {reason}", file=sys.stderr)
            return 1
        except OSError as trouble:
            # 読む先や書く先の権限、容量。追記なら下書きは差し替え前のまま残っている。
            print(f"下書きを読み書きできません: {trouble}", file=sys.stderr)
            return 1
        return 0

    def _drafted(self, draft: Draft, drafting: Drafting) -> None:
        self._say(f"候補を引いた {drafting.drawn_with().described}")
        self._say(
            f"記録: {draft.sessions} セッション、中身のある発話 {draft.spoken} 件"
            + f"（読めず飛ばしたファイル {len(draft.skipped)}）"
        )
        self._say(f"覚えさせる発話: {draft.exchanges} 件")
        self._say(f"問: {draft.cases} 問（後の発話が前の話題を指していそうなもの。すべて確認前）")
        self._say(f"  {self._out} の各問を読み、残す問は confirmed = true にしてください")
        self._say(NOT_CAUGHT)

    def _appended(self, appended: Appended, drafting: Drafting) -> None:
        covered = appended.covered
        self._say(f"候補を引いた {drafting.drawn_with().described}")
        if appended.notice:
            self._say(f"注意: {appended.notice}")
        self._say(
            f"前回の範囲: {covered.until.astimezone().strftime('%Y-%m-%d %H:%M')} まで、"
            + f"{'、'.join(covered.places)}、飛ばしたファイル {len(covered.skipped)}、"
            + f"{covered.sessions} セッション"
        )
        self._say(
            f"前回の下書き: 覚えさせる発話 {appended.previous_exchanges} 件、"
            + f"問 {appended.previous_cases} 問"
        )
        self._say(
            f"今回: 新しい記録 {appended.new_sessions} セッション、"
            + f"新しい発話 {appended.incoming} 件"
            + f"（読めず飛ばしたファイル {len(appended.skipped)}）、"
            + f"判定へ渡した発話 {appended.asked} 件、"
            + f"候補が無く渡さなかった発話 {appended.unasked} 件"
        )
        self._say(f"覚えさせる発話: +{appended.added_exchanges} 件")
        untouched = f"前回の {appended.previous_cases} 問とその確認はそのまま"
        if appended.added_cases:
            self._say(f"問: +{appended.added_cases} 問（すべて確認前）。{untouched}")
            self._say(f"  {self._out} の新しい問を読み、残す問は confirmed = true にしてください")
        else:
            self._say(f"問: 増えませんでした。前回の範囲だけを進めました。{untouched}")
        self._say(NOT_CAUGHT)

    def _say(self, line: str) -> None:
        _ = self._writing.write(f"{line}\n")
