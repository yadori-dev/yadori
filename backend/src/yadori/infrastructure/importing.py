"""選んだ通常ログを確認し、明示された人物へ取り込む端末入口。"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, final

from yadori.adapter.embedding import Announcing, DefaultEmbeddings
from yadori.adapter.importing.archive import SqliteArchive
from yadori.adapter.importing.records import SessionLogs
from yadori.domain.importing.model import ExternalConversation, ImportFailed, Provider
from yadori.domain.memory import Embeddings, EmbeddingsUnavailable
from yadori.infrastructure.settings import NotSettled, Settings, SettingsFile
from yadori.usecase.importing.service import Importing


@dataclass(frozen=True)
class ImportSource:
    provider: Provider
    path: Path


@final
class Importer:
    def __init__(
        self,
        home: Path | None = None,
        writing: TextIO | None = None,
        default: Callable[[Path | None, Announcing | None], Embeddings] | None = None,
    ) -> None:
        self._settings = SettingsFile(home)
        self._writing = writing or sys.stdout
        self._default = default or DefaultEmbeddings()

    def run(
        self,
        sources: Sequence[ImportSource],
        person: str,
        apply: bool = False,
        limit: int | None = None,
        settings: Settings | None = None,
    ) -> int:
        importing: Importing | None = None
        pending = 0
        try:
            selected = settings or self._settings.read(person)
            if selected.dweller.id != person:
                raise NotSettled("取り込み先の人物と固定した設定が一致しません")
            self._say(f"取り込み先: {selected.dweller.name}（{selected.dweller.id}）")
            records: list[ExternalConversation] = []
            logs = SessionLogs()
            for source in sources:
                files = logs.files(source.provider, source.path.expanduser())
                self._say(f"取り込み元: {source.provider} / {source.path}（{len(files)}ファイル）")
                for path in files:
                    contents = logs.read(source.provider, path)
                    records.extend(contents.conversations)
                    for notice, count in Counter(contents.notices).items():
                        repeated = f"（同じ理由の発生: {count} 回）" if count > 1 else ""
                        self._say(f"  {path.name}: {notice}{repeated}")
            importing = Importing(SqliteArchive(selected.memories_path), person)
            plan = importing.preview(records)
            pending = len(plan.added)
            self._say(
                f"追加予定: {pending} 件 / 既存: {plan.existing} 件"
                + f" / 宿り自身の記録: {plan.native} 件"
            )
            for record in plan.added[:10]:
                self._say(
                    f"  {record.provider} {record.happened_at.isoformat()} 会話 {record.session}: "
                    + record.utterance[:60].replace(chr(10), " ")
                )
            if not apply:
                self._say(
                    "確認のみ。記憶は変更していません。保存する場合は --apply を付けてください"
                )
                return 0
            if not pending:
                self._say("取り込み: 新しい完成会話はありません")
                return 0
            embeddings = self._default(selected.models_path, self._say)
            _ = importing.apply(
                plan,
                embeddings,
                limit,
                lambda saved, total: self._say(f"取り込み中: {saved}/{total} 件"),
            )
            self._say(
                f"取り込み完了: {importing.saved} 件 / 残り: {max(0, pending - importing.saved)} 件"
            )
            return 0
        except (ImportFailed, NotSettled, OSError, sqlite3.Error, EmbeddingsUnavailable) as trouble:
            saved = 0 if importing is None else importing.saved
            self._say(
                f"取り込み失敗: {trouble}\n確定済み: {saved} 件"
                + f" / 未処理予定: {max(0, pending - saved)} 件。再実行できます"
            )
            return 1
        except KeyboardInterrupt:
            saved = 0 if importing is None else importing.saved
            self._say(f"取り込みを中断しました。確定済み: {saved} 件。残りは次回に取り込めます")
            raise

    def _say(self, message: str) -> None:
        _ = self._writing.write(message + "\n")
        _ = self._writing.flush()
