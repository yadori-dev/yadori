"""宿りを起こして、話しかけられるのを待つ。

ここがプロセスの入口である。設定を読み、部品を組み、話す場所へ繋ぐ。
記憶の規則も応対の作り方もここには無い。
"""

from __future__ import annotations

import sys

from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.place import Terminal
from yadori.adapter.store import SqliteMemories
from yadori.adapter.voice import ClaudeVoice
from yadori.domain.memory import Memories
from yadori.infrastructure.settings import NotSettled, Settings, read_settings
from yadori.usecase.conversation import Conversation, Turn


def main() -> int:
    """宿りを起こして、話しかけられるのを待つ。

    - 設定を読む
    - 記憶を開く
    - 宿りを住まわせ、名乗りを確かめる
    - 一往復の手順を組む
    - 話す場所へ繋いで待つ
    """
    try:
        settings = read_settings()
    except NotSettled as missing:
        print(missing, file=sys.stderr)
        return 1

    memories = SqliteMemories(settings.memories_path)
    try:
        settle(memories, settings)
        Terminal(assemble(memories), settings.dweller).listen()
    finally:
        memories.close()
    return 0


def settle(memories: SqliteMemories, settings: Settings) -> None:
    """宿りを住まわせ、手元の名乗りを現在の版にする。

    名乗りの文章が変わっていれば新しい版を足す。以前の版は消さない。
    """
    memories.settle(settings.dweller)
    current = memories.current_identity(settings.dweller.id)
    if current is None or current.text != settings.name_declared:
        memories.write_identity(settings.dweller.id, settings.name_declared)


def assemble(memories: Memories) -> Turn:
    """思い出すと覚えるを繋いで、一往復の手順にする。

    埋め込みは文字の並びから作る実装を使う。応対の文章は模型が作る。
    """
    from datetime import UTC, datetime

    conversation = Conversation(memories, CharacterPairs(), lambda: datetime.now(UTC))
    return Turn(conversation, ClaudeVoice())
