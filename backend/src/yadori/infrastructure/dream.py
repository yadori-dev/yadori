"""夢を起こす入口。前回より後の記憶を読み直し、選び、要点を残して、何をしたかを書く。"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, final

from yadori.adapter.dream import ClaudeCodeSummarizing
from yadori.adapter.dream.clarification import ToolExplaining
from yadori.adapter.embedding import Announcing, DefaultEmbeddings
from yadori.adapter.store import SqliteMemories
from yadori.domain.dream import CannotDream, Summarizing
from yadori.domain.dream.clarification import Explaining
from yadori.domain.memory import Embeddings, EmbeddingsUnavailable, HowToRecall, NameNotDeclared
from yadori.infrastructure.settings import NotSettled, Settings, SettingsFile
from yadori.infrastructure.start import Startup
from yadori.infrastructure.tools import SelectedCall
from yadori.usecase.dream import Dreaming, Dreamt, NothingKept, NothingNew
from yadori.usecase.dream.clarification import Clarified, Clarifying

# 選んだ往復は一晩分でも十数件になる。応対より長めに待つ。
SUMMARIZE_WAIT_SECONDS = 300


@final
class Dreamer:
    def __init__(
        self,
        home: Path | None = None,
        summarizing: Summarizing | None = None,
        default: Callable[[Path | None, Announcing | None], Embeddings] | None = None,
        how: HowToRecall | None = None,
        writing: TextIO | None = None,
        explaining: Explaining | None = None,
    ) -> None:
        self._settings_file: SettingsFile = SettingsFile(home)
        self._summarizing: Summarizing | None = summarizing
        self._explaining: Explaining | None = explaining
        self._default: Callable[[Path | None, Announcing | None], Embeddings] = (
            default or DefaultEmbeddings()
        )
        # 同じ話題の繰り返しを見る近さの下限。会話と同じ既定を使う。差し替えの埋め込みで
        # 確かめるテストは、その埋め込みに合う下限を渡す。
        self._how: HowToRecall | None = how
        self._writing: TextIO = writing or sys.stdout

    def run(self, settings: Settings | None = None) -> int:
        """夢を見て、結果を書く。

        設定が無い、要点を書けない、名乗りが無い、埋め込みが使えないときは理由を書いて 1。
        """
        try:
            settings = settings or self._settings_file.read()
        except NotSettled as missing:
            print(missing, file=sys.stderr)
            return 1
        memories = SqliteMemories(settings.memories_path)
        try:
            Startup(settings.home).settle(memories, settings)
            summarizing = self._summarizing or ClaudeCodeSummarizing(
                SelectedCall("夢の要約", SUMMARIZE_WAIT_SECONDS, settings.home)
            )
            embeddings = self._default(settings.models_path, print)
            if not self._clarify(memories, embeddings, settings.dweller.id, settings.home):
                return 1
            dreamt = Dreaming(
                memories, embeddings, summarizing, lambda: datetime.now(UTC), self._how
            ).run(settings.dweller.id)
        except (NotSettled, CannotDream, NameNotDeclared, EmbeddingsUnavailable) as reason:
            print(f"夢を見られません: {reason}", file=sys.stderr)
            return 1
        finally:
            memories.close()
        self._write(dreamt)
        return 0

    def _clarify(
        self, memories: SqliteMemories, embeddings: Embeddings, dweller_id: str, home: Path
    ) -> bool:
        explaining = self._explaining or ToolExplaining(
            SelectedCall("夢の補完", SUMMARIZE_WAIT_SECONDS, home)
        )
        clarifying = Clarifying(memories, embeddings, explaining, lambda: datetime.now(UTC))
        try:
            result = clarifying.run(dweller_id)
        except (CannotDream, EmbeddingsUnavailable, sqlite3.Error, OSError) as trouble:
            self._write_clarified(Clarified(tuple(clarifying.records), True), memories, failed=1)
            print(f"補完を続けられません。未処理は次回再試行します: {trouble}", file=sys.stderr)
            return False
        self._write_clarified(result, memories)
        return True

    def _write_clarified(
        self, result: Clarified, memories: SqliteMemories, failed: int = 0
    ) -> None:
        self._say(
            f"返答の補完: {sum(one.status == 'clarified' for one in result.records)} 件、"
            + f"保留: {sum(one.status == 'deferred' for one in result.records)} 件、"
            + f"対象外: {sum(one.status == 'unneeded' for one in result.records)} 件、"
            + f"失敗: {failed} 件、未処理: {'あり' if result.remaining else 'なし'}"
        )
        for record in result.records:
            self._say(f"  往復 {record.episode_id}: {record.text or record.reason}")
            if record.status == "clarified":
                self._say("    推定の説明です。承認を完了や将来の操作許可には広げません")
                for source in (record.episode_id, *record.sources):
                    original = memories.episode(source)
                    if original is not None:
                        self._say(f"    原文 {source}: {original.utterance} / {original.reply}")

    def _write(self, dreamt: Dreamt | NothingKept | NothingNew) -> None:
        if isinstance(dreamt, NothingNew):
            self._say("夢: 新しい記憶がありません。読み直しませんでした")
            return
        dream = dreamt.dream
        read_from = f"{dream.read_from.astimezone():%Y-%m-%d %H:%M}"
        read_to = f"{dream.read_to.astimezone():%H:%M}"
        if isinstance(dreamt, NothingKept):
            self._say(
                f"夢: {read_from} から {read_to} までの {dream.count} 件を読みましたが、"
                + "残すものはありませんでした"
            )
            return
        self._say(
            f"夢: {read_from} から {read_to} までの {dream.count} 件を読み、"
            + f"{dream.kept} 件を選びました"
        )
        self._say(f"要点: {len(dreamt.gists)} 件を残しました")
        for gist in dreamt.gists:
            self._say(f"  - {gist.text}")
        self._say(f"気づき: {dream.noticing or '無し'}")
        self._say(
            f"選んだ {dream.kept} 件になぞった記録を残しました"
            + "（思い出す順に効くのは、思い出しやすさの増分から）"
        )

    def _say(self, line: str) -> None:
        _ = self._writing.write(f"{line}\n")
