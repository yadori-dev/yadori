"""宿りを起こして、話しかけられるのを待つ。

ここがプロセスの入口である。設定を読み、部品を組み、話す場所へ繋ぐ。
記憶の規則も応対の作り方もここには無い。
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import final

from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.place import Terminal
from yadori.adapter.store import SqliteMemories
from yadori.adapter.voice import ClaudeVoice
from yadori.infrastructure.settings import NotSettled, Settings, SettingsFile
from yadori.usecase.conversation import Conversation, Turn


@final
class Startup:
    """宿りを起こして、話しかけられるのを待つ。"""

    def __init__(self, home: Path | None = None) -> None:
        self._settings_file: SettingsFile = SettingsFile(home)

    def run(self) -> int:
        """起こして、待つ。

        - 設定を読む
        - 記憶を開く
        - 宿りを住まわせ、名乗りを確かめる
        - 一往復の手順を組む
        - 話す場所へ繋いで待つ
        """
        try:
            settings = self._settings_file.read()
        except NotSettled as missing:
            return self._refuse(missing)

        memories = SqliteMemories(settings.memories_path)
        try:
            self._settle(memories, settings)
            Terminal(self._assemble(memories), settings.dweller).listen()
        finally:
            memories.close()
        return 0

    def _refuse(self, missing: NotSettled) -> int:
        """起こせない理由を書いて、何も作らずに終わる。"""
        print(missing, file=sys.stderr)
        return 1

    def _settle(self, memories: SqliteMemories, settings: Settings) -> None:
        """宿りを住まわせ、手元の名乗りを現在の版にする。

        名乗りの文章が変わっていれば新しい版を足す。以前の版は消さない。
        """
        memories.settle(settings.dweller)
        current = memories.current_identity(settings.dweller.id)
        if current is None or current.text != settings.name_declared:
            _ = memories.write_identity(settings.dweller.id, settings.name_declared)

    def _assemble(self, memories: SqliteMemories) -> Turn:
        """思い出すと覚えるを繋いで、一往復の手順にする。

        埋め込みは文字の並びから作る実装を使う。応対の文章は模型が作る。
        """
        conversation = Conversation(memories, CharacterPairs(), self._now)
        return Turn(conversation, ClaudeVoice())

    def _now(self) -> datetime:
        return datetime.now(UTC)
