"""思い出すと覚える。

どちらも宿りを受け取る。返事を誰が作るかは出ていく先で異なるため、この層は
返事を受け取るだけで作らない。

口の中身は、上から読めば何をする手順かが並ぶように書く。各段の詳しさは
一段ずつ下げ、同じ段に別の粒度を混ぜない。
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from datetime import datetime
from typing import final

from yadori.domain.dream.clarification import REVISION
from yadori.domain.importing.model import ExternalFound
from yadori.domain.importing.ports import Archive
from yadori.domain.memory import (
    Dreamed,
    Embeddings,
    Episode,
    Found,
    HowToRecall,
    Identity,
    Memories,
    Moved,
    NameNotDeclared,
    Recollection,
    RememberingConflict,
    State,
    Vector,
)
from yadori.usecase.importing.searching import Searching


@final
class Conversation:
    """会話の口。思い出すと覚えるの二つが一組である。"""

    def __init__(
        self,
        memories: Memories,
        embeddings: Embeddings | Sequence[Embeddings],
        now: Callable[[], datetime],
        how: HowToRecall | None = None,
        archive: Archive | None = None,
    ) -> None:
        self._archive = archive
        self._memories: Memories = memories
        self._ways: tuple[Embeddings, ...] = (
            tuple(embeddings) if isinstance(embeddings, Sequence) else (embeddings,)
        )
        if not self._ways:
            # 道が無いと、原文だけ書かれてインデックスが作られず、思い出すと常に空になる。
            raise ValueError("思い出す道が一つも無い")
        self._now: Callable[[], datetime] = now
        self._how: HowToRecall = how or HowToRecall()

    def recall(self, dweller_id: str, utterance: str) -> Recollection:
        """話しかけられた文章から、応対を作る前に渡すものを組み立てる。

        - 名乗りを確かめる
        - 直近のやりとりを取る
        - それより前から意味で探す
        - 今の状態（気持ちと性格）を求める
        - 最新の夢を添える
        - 思い出したことを記録する
        """
        recalled_at = self._now()
        identity = self._declared_identity(dweller_id)
        recent = self._recent(dweller_id)
        candidates = Searching(self._memories, self._archive, self._how).find(
            self._ways, dweller_id, utterance, [one.id for one in recent]
        )
        found = tuple(one for one in candidates if isinstance(one, Found))
        external = tuple(one for one in candidates if isinstance(one, ExternalFound))
        state = self.state(dweller_id, recalled_at)
        dream = self._dreamed(dweller_id)
        self._record_retrieval(found, recalled_at)
        return Recollection(
            identity=identity,
            recent=recent,
            found=found,
            state=state,
            dream=dream,
            recalled_at=recalled_at,
            external=external,
        )

    def remember(
        self,
        dweller_id: str,
        utterance: str,
        reply: str,
        moved: Moved | None = None,
        *,
        recalled_at: datetime | None = None,
        source: str | None = None,
        identity_version: int | None = None,
        session_id: str | None = None,
        previous_source: str | None = None,
    ) -> Episode:
        """一度のやりとりを、原文のまま記憶へ加え、気持ちを動かす。

        - 名乗りを確かめる（無ければ原文を書く前に断る）
        - 数の並びを先に作る（埋め込みを使えなければここで断る）
        - 原文と動きをひとまとまりで確定する
        - 後から作り直せるインデックスを書く
        """
        identity = self._identity_used(dweller_id, identity_version)
        made = self._made(utterance)
        happened_at = self._now()
        return self._memories.keep_episode(
            dweller_id,
            utterance,
            reply,
            identity.version,
            happened_at,
            recalled_at,
            source,
            made,
            moved,
            session_id,
            previous_source,
        )

    def _dreamed(self, dweller_id: str) -> Dreamed | None:
        """最新の夢と、その夢で残した要点。夢が無ければ無し。"""
        dream = self._memories.latest_dream(dweller_id)
        if dream is None:
            return None
        return Dreamed(dream=dream, gists=self._memories.gists_of_dream(dream.id))

    def state(self, dweller_id: str, at: datetime | None = None) -> State:
        """今の状態。気持ちと性格を、積まれた動きと経過時間から求め、保存しない（ADR-007）。"""
        return State.from_shifts(self._memories.shifts(dweller_id), at or self._now())

    def rebuild_index(self, dweller_id: str) -> int:
        """インデックスを原文と保存済みの補完から作り直す。

        - インデックスを持たない原文を集める
        - 一件ずつインデックスを作る

        原文は読むだけで変えない。
        """
        rebuilt = 0
        for way in self._ways:
            for episode in self._memories.episodes_without_index(dweller_id, way.name):
                self._memories.write_index(episode.id, way.name, way.to_remember(episode.utterance))
                rebuilt += 1
            for record in self._memories.clarifications_without_index(
                dweller_id, way.name, REVISION
            ):
                episode = self._memories.episode(record.episode_id)
                if episode is None:
                    raise RuntimeError("補完の原文が見つからない")
                self._memories.write_clarification_index(
                    episode.id,
                    record.revision,
                    way.name,
                    way.to_remember(record.searchable(episode)),
                )
                rebuilt += 1
        if self._archive is not None:
            for way in self._ways:
                for record in self._archive.unindexed(dweller_id, way.name):
                    self._archive.index(
                        dweller_id,
                        record.id,
                        way.name,
                        way.to_remember(record.conversation.utterance),
                    )
                    rebuilt += 1
        return rebuilt

    # 思い出す

    def _declared_identity(self, dweller_id: str) -> Identity:
        """名乗りを取る。無ければ応対を作れないため断る。"""
        identity = self._memories.current_identity(dweller_id)
        if identity is None:
            raise NameNotDeclared(dweller_id)
        return identity

    def _identity_used(self, dweller_id: str, version: int | None) -> Identity:
        """返事を作った時点の名乗りを取る。途中で名乗りが変わっても現在値へ置き換えない。"""
        if version is None:
            return self._declared_identity(dweller_id)
        identity = self._memories.identity_at(dweller_id, version)
        if identity is None:
            raise RememberingConflict(f"返事を作った名乗りの版が見つからない: {version}")
        return identity

    def _recent(self, dweller_id: str) -> tuple[Episode, ...]:
        """直近のやりとりを、意味を見ずに新しい順で取る。

        指す語だけの発話は意味で探しても何も出ないため、この道で渡す。
        """
        return self._memories.recent(dweller_id, self._how.recent_turns)

    def _record_retrieval(self, found: Collection[Found], at: datetime) -> None:
        """思い出したことを記録する。思い出しやすさはここから求める。"""
        self._memories.record_retrieval([one.episode.id for one in found], at)

    # 覚える

    def _made(self, utterance: str) -> tuple[tuple[str, Vector], ...]:
        """道ごとに数の並びを作る。

        原文を書く前に呼ぶ。埋め込みを使えないときは、ここで断って何も書かない。
        """
        return tuple((way.name, way.to_remember(utterance)) for way in self._ways)
