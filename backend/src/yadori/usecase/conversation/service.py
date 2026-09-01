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

from yadori.domain.memory import (
    Embeddings,
    Episode,
    Found,
    HowToRecall,
    Identity,
    Memories,
    NameNotDeclared,
    Recollection,
    Vector,
)


@final
class Conversation:
    """会話の口。思い出すと覚えるの二つが一組である。"""

    def __init__(
        self,
        memories: Memories,
        embeddings: Embeddings | Sequence[Embeddings],
        now: Callable[[], datetime],
        how: HowToRecall | None = None,
    ) -> None:
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
        - 思い出したことを記録する
        """
        identity = self._declared_identity(dweller_id)
        recent = self._recent(dweller_id)
        found = self._found_beyond(dweller_id, utterance, recent)
        self._record_retrieval(found)
        return Recollection(identity=identity, recent=recent, found=found)

    def remember(self, dweller_id: str, utterance: str, reply: str) -> Episode:
        """一度のやりとりを、原文のまま記憶へ加える。

        - 名乗りを確かめる（無ければ原文を書く前に断る）
        - 数の並びを先に作る（埋め込みを使えなければここで断る）
        - 原文を確定する
        - インデックスを書く
        """
        identity = self._declared_identity(dweller_id)
        made = self._made(utterance)
        episode = self._keep_episode(dweller_id, utterance, reply, identity)
        self._write_index(episode, made)
        return episode

    def rebuild_index(self, dweller_id: str) -> int:
        """インデックスを原文から作り直す。

        - インデックスを持たない原文を集める
        - 一件ずつインデックスを作る

        原文は読むだけで変えない。
        """
        rebuilt = 0
        for way in self._ways:
            for episode in self._memories.episodes_without_index(dweller_id, way.name):
                self._memories.write_index(episode.id, way.name, way.to_remember(episode.utterance))
                rebuilt += 1
        return rebuilt

    # 思い出す

    def _declared_identity(self, dweller_id: str) -> Identity:
        """名乗りを取る。無ければ応対を作れないため断る。"""
        identity = self._memories.current_identity(dweller_id)
        if identity is None:
            raise NameNotDeclared(dweller_id)
        return identity

    def _recent(self, dweller_id: str) -> tuple[Episode, ...]:
        """直近のやりとりを、意味を見ずに新しい順で取る。

        指す語だけの発話は意味で探しても何も出ないため、この道で渡す。
        """
        return self._memories.recent(dweller_id, self._how.recent_turns)

    def _found_beyond(
        self, dweller_id: str, utterance: str, recent: Collection[Episode]
    ) -> tuple[Found, ...]:
        """直近より前から、意味の近さで探す。

        - 直近で渡すものを除く
        - 近さと思い出した記録を別の値として持たせる

        同じ記憶が二つの道で現れると、何が効いたのかを読めなくなる。
        """
        skip = [episode.id for episode in recent]
        by_way = [self._by(way, dweller_id, utterance, skip) for way in self._ways]
        return self._woven(by_way)

    def _by(
        self, way: Embeddings, dweller_id: str, utterance: str, skip: list[int]
    ) -> tuple[Found, ...]:
        """一つの道で探す。"""
        hits = self._memories.search(
            dweller_id,
            way.name,
            way.to_recall(utterance),
            self._how.found_limit,
            self._how.relevance_floor,
            exclude=skip,
        )
        return tuple(
            self._with_retrieval(episode, relevance, way.name) for episode, relevance in hits
        )

    def _woven(self, by_way: list[tuple[Found, ...]]) -> tuple[Found, ...]:
        """道ごとの結果を、順位の高いものから交互に並べる。

        点数を混ぜない。混ぜると、どの道が効いたかを読めなくなる。同じ記憶が
        二つの道で出たら、先に出たほうだけを渡す。
        """
        woven: list[Found] = []
        seen: set[int] = set()
        for place in range(self._how.found_limit):
            for found in by_way:
                if place < len(found) and found[place].episode.id not in seen:
                    seen.add(found[place].episode.id)
                    woven.append(found[place])
        return tuple(woven[: self._how.found_limit])

    def _with_retrieval(self, episode: Episode, relevance: float, way: str) -> Found:
        """近さと思い出した記録を、別の値として並べる。一つの点数へ混ぜない。"""
        return Found(
            episode=episode,
            relevance=relevance,
            retrieval=self._memories.retrieval(episode.id),
            way=way,
        )

    def _record_retrieval(self, found: Collection[Found]) -> None:
        """思い出したことを記録する。思い出しやすさはここから求める。"""
        self._memories.record_retrieval([one.episode.id for one in found], self._now())

    # 覚える

    def _keep_episode(
        self, dweller_id: str, utterance: str, reply: str, identity: Identity
    ) -> Episode:
        """原文をそのまま確定する。どの名乗りで作られた応対かも一緒に残す。"""
        return self._memories.write_episode(
            dweller_id, utterance, reply, identity.version, self._now()
        )

    def _made(self, utterance: str) -> tuple[tuple[str, Vector], ...]:
        """道ごとに数の並びを作る。

        原文を書く前に呼ぶ。埋め込みを使えないときは、ここで断って何も書かない。
        """
        return tuple((way.name, way.to_remember(utterance)) for way in self._ways)

    def _write_index(self, episode: Episode, made: tuple[tuple[str, Vector], ...]) -> None:
        """作っておいた数の並びをインデックスとして書く。

        原文を確定した後に呼ぶ。ここで失敗しても原文は残り、後から作り直せる。
        """
        for name, vector in made:
            self._memories.write_index(episode.id, name, vector)
