"""宿りを起こして、話しかけられるのを待つ。

ここがプロセスの入口である。設定を読み、部品を組み、話す場所へ繋ぐ。
記憶の規則も応対の作り方もここには無い。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import final

from yadori.adapter.embedding import Announcing, DefaultEmbeddings
from yadori.adapter.place import Terminal
from yadori.adapter.store import SqliteMemories
from yadori.adapter.tool import ClaudeCodeCall
from yadori.adapter.voice import WAIT_SECONDS, ClaudeCodeVoice
from yadori.domain.memory import Embeddings, EmbeddingsUnavailable
from yadori.infrastructure.settings import NotSettled, Settings, SettingsFile
from yadori.usecase.conversation import Conversation, Turn

Factory = Callable[[Path | None, Announcing | None], Embeddings]


@final
class Startup:
    """宿りを起こして、話しかけられるのを待つ。"""

    def __init__(self, home: Path | None = None, default: Factory | None = None) -> None:
        self._settings_file: SettingsFile = SettingsFile(home)
        # 既定の埋め込みは工場が組む。下書きの入口と名前で選ぶ部品も同じ工場を使う。
        self._default: Factory = default or DefaultEmbeddings()

    def run(self) -> int:
        """起こして、待つ。

        - 設定を読む
        - 記憶を開く
        - 宿りを住まわせ、名乗りを確かめる
        - 一往復の手順を組む
        - いまの埋め込みのインデックスが無い記憶を作り直す
        - 話す場所へ繋いで待つ
        """
        try:
            settings = self._settings_file.read()
        except NotSettled as missing:
            return self._refuse(missing)

        memories = SqliteMemories(settings.memories_path)
        try:
            self.settle(memories, settings)
            turn = self._assemble(memories, settings)
            self._catch_up(turn, settings)
            Terminal(turn, settings.dweller).listen()
        except EmbeddingsUnavailable as missing:
            return self._refuse(missing)
        finally:
            memories.close()
        return 0

    def _refuse(self, missing: NotSettled | EmbeddingsUnavailable) -> int:
        """起こせない理由を書いて、記憶を増やさずに終わる。何を用意すればよいかは理由が持つ。"""
        print(missing, file=sys.stderr)
        return 1

    def settle(self, memories: SqliteMemories, settings: Settings) -> None:
        """宿りを住まわせ、手元の名乗りを現在の版にする。

        名乗りの文章が変わっていれば新しい版を足す。以前の版は消さない。
        """
        memories.settle(settings.dweller)
        current = memories.current_identity(settings.dweller.id)
        if current is None or current.text != settings.name_declared:
            _ = memories.write_identity(settings.dweller.id, settings.name_declared)

    def _catch_up(self, turn: Turn, settings: Settings) -> None:
        """いまの埋め込みのインデックスを持たない記憶へ、インデックスを作る。

        埋め込みを替えると、それまでのインデックスは使えない。原文は残っているため、
        ここで作り直せば以前の記憶も新しい埋め込みで探せる。
        """
        rebuilt = turn.rebuild_index(settings.dweller.id)
        if rebuilt:
            print(f"（{rebuilt}件の記憶へ、いまの埋め込みでインデックスを作りました）")

    def _assemble(self, memories: SqliteMemories, settings: Settings) -> Turn:
        """思い出すと覚えるを繋いで、一往復の手順にする。

        埋め込みは既定の工場が組む。AIモデルのファイルは `YADORI_HOME` の下の `models/` に
        保存し、初回の取得が黙って進まないよう前触れは画面へ出す。
        応対の文章は、持ち主の定額契約で動く対話する道具が作る。
        """
        conversation = Conversation(memories, self.embeddings(settings), self._now)
        return Turn(conversation, ClaudeCodeVoice(ClaudeCodeCall(settings.model, WAIT_SECONDS)))

    def embeddings(self, settings: Settings) -> Embeddings:
        """宿りが使う埋め込み。既定の工場が組む。"""
        return self._default(settings.models_path, print)

    def _now(self) -> datetime:
        return datetime.now(UTC)
