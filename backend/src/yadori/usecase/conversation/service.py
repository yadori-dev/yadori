"""思い出すと覚える。

どちらも宿りを受け取る。返事を誰が作るかは出ていく先で異なるため、この層は
返事を受け取るだけで作らない。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from yadori.domain.memory import (
    Embeddings,
    Episode,
    HowToRecall,
    Memories,
    NameNotDeclared,
    Recollection,
    recollect,
)


class Conversation:
    def __init__(
        self,
        memories: Memories,
        embeddings: Embeddings,
        now: Callable[[], datetime],
        how: HowToRecall | None = None,
    ) -> None:
        self._memories = memories
        self._embeddings = embeddings
        self._now = now
        self._how = how or HowToRecall()

    def recall(self, dweller_id: str, utterance: str) -> Recollection:
        """話しかけられた文章から、関係する記憶と名乗りを取り出す。"""
        return recollect(
            self._memories,
            self._embeddings,
            dweller_id,
            utterance,
            self._now(),
            self._how,
        )

    def remember(self, dweller_id: str, utterance: str, reply: str) -> Episode:
        """一度のやりとりを、原文のまま記憶へ加える。

        名乗りが無ければ書く前に断る。原文を確定してから索引を作るため、索引の
        作成に失敗しても原文は残り、後から作り直せる。
        """
        identity = self._memories.current_identity(dweller_id)
        if identity is None:
            raise NameNotDeclared(dweller_id)

        episode = self._memories.write_episode(
            dweller_id, utterance, reply, identity.version, self._now()
        )
        self._memories.write_index(
            episode.id, self._embeddings.name, self._embeddings.of(utterance)
        )
        return episode

    def rebuild_index(self, dweller_id: str) -> int:
        """索引を原文から作り直す。原文は読むだけで変えない。"""
        rebuilt = 0
        for episode in self._memories.episodes_without_index(dweller_id):
            self._memories.write_index(
                episode.id, self._embeddings.name, self._embeddings.of(episode.utterance)
            )
            rebuilt += 1
        return rebuilt
