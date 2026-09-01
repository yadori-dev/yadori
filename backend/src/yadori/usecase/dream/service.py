"""夢。前回の夢より後の記憶を読み直し、残すものを選び、要点の層を作り、選んだものをなぞる。

原文は読むだけで変えない。要点を書く相手は口として受け、書けなければ何も積まない。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import final

from yadori.domain.dream import Candidate, Keeping, Summarizing
from yadori.domain.memory import (
    Dream,
    Embeddings,
    Episode,
    Gist,
    HowToRecall,
    Identity,
    Memories,
    NameNotDeclared,
)


@dataclass(frozen=True)
class Dreamt:
    """夢を見た結果。残した記録と要点。"""

    dream: Dream
    gists: tuple[Gist, ...]


@dataclass(frozen=True)
class NothingKept:
    """読み直したが、残すものが無かった。読んだ範囲だけを記録した。"""

    dream: Dream


@dataclass(frozen=True)
class NothingNew:
    """新しい記憶が無く、読み直さなかった。"""


# 一度の夢で読む上限。初回や長く空いた後に全部を読むと、要点を書く道具の長さの上限で失敗し、
# 記録が残らないため次も同じ範囲を読んで抜け出せなくなる。上限に達したらそこまでを読んだ範囲と
# して記録し、残りは次の夢で読む。
READ_LIMIT = 100


@final
class Dreaming:
    def __init__(
        self,
        memories: Memories,
        embeddings: Embeddings,
        summarizing: Summarizing,
        now: Callable[[], datetime],
        how: HowToRecall | None = None,
    ) -> None:
        self._memories: Memories = memories
        self._embeddings: Embeddings = embeddings
        self._summarizing: Summarizing = summarizing
        self._now: Callable[[], datetime] = now
        self._how: HowToRecall = how or HowToRecall()

    def run(self, dweller_id: str) -> Dreamt | NothingKept | NothingNew:
        """夢を見る。

        - 前回の夢より後の記憶を古い順に集める（無ければ読み直さない）
        - 残すものを選ぶ（無ければ読んだ範囲だけ記録する）
        - 名乗りを添えて要点と気づきを書かせる
        - 記録と要点を積み、選んだ往復に思い出した記録を積む
        """
        episodes = self._memories.episodes_after(dweller_id, self._since(dweller_id))[:READ_LIMIT]
        if not episodes:
            return NothingNew()
        kept = Keeping().keep([self._candidate(dweller_id, one, episodes) for one in episodes])
        if not kept:
            return NothingKept(self._recorded(dweller_id, episodes, kept, None))
        summarized = self._summarizing.summarize(self._identity(dweller_id), kept)
        dream = self._recorded(dweller_id, episodes, kept, summarized.noticing)
        gists = self._gists(dweller_id, dream, kept, summarized.gists)
        self._memories.record_retrieval([one.id for one in kept], dream.at)
        return Dreamt(dream=dream, gists=gists)

    def _since(self, dweller_id: str) -> datetime | None:
        latest = self._memories.latest_dream(dweller_id)
        return None if latest is None else latest.read_to

    def _candidate(self, dweller_id: str, episode: Episode, read: tuple[Episode, ...]) -> Candidate:
        """一往復について、思い出されたか、どれだけ動いたか、同じ話題が繰り返したかを集める。"""
        return Candidate(
            episode=episode,
            retrieved=self._memories.retrieval(episode.id).count > 0,
            shift=self._shift_of(dweller_id, episode),
            repeated=self._repeated(dweller_id, episode, read),
        )

    def _shift_of(self, dweller_id: str, episode: Episode) -> float:
        # 一往復に積まれる動きは一件（覚える口が一度だけ積む）。
        for one in self._memories.shifts(dweller_id):
            if one.episode_id == episode.id:
                return one.delta
        return 0.0

    def _repeated(self, dweller_id: str, episode: Episode, read: tuple[Episode, ...]) -> bool:
        """この発話で話しかけられたら同じ範囲の別の往復を思い出すなら、繰り返し出た話題とみなす。

        会話で思い出すのと同じ条件（問い合わせ側の並びと覚える側の並び、同じ下限）で見る。
        覚える側どうしで比べると、意味を見る埋め込みでは日常の文がどれも近く出て、選ぶ働きを
        失う（実走で 11 件中 9 件が選ばれた）。範囲の外の古い記憶が上位を埋めても数え漏れない
        ように、下限以上を全部受け取ってから範囲の中だけを見る。
        """
        others = {one.id for one in read} - {episode.id}
        hits = self._memories.search(
            dweller_id,
            self._embeddings.name,
            self._embeddings.to_recall(episode.utterance),
            self._memories.count_episodes(dweller_id),
            self._how.relevance_floor,
            exclude=(episode.id,),
        )
        return any(found.id in others for found, _ in hits)

    def _identity(self, dweller_id: str) -> Identity:
        identity = self._memories.current_identity(dweller_id)
        if identity is None:
            raise NameNotDeclared(dweller_id)
        return identity

    def _recorded(
        self,
        dweller_id: str,
        read: tuple[Episode, ...],
        kept: tuple[Episode, ...],
        noticing: str | None,
    ) -> Dream:
        return self._memories.record_dream(
            dweller_id,
            at=self._now(),
            read_from=read[0].happened_at,
            read_to=read[-1].happened_at,
            count=len(read),
            kept=len(kept),
            noticing=noticing,
        )

    def _gists(
        self, dweller_id: str, dream: Dream, kept: tuple[Episode, ...], texts: tuple[str, ...]
    ) -> tuple[Gist, ...]:
        """要点を、選んだ往復と夢に結んで積む。

        どの要点がどの往復から出たかは道具が答えないので、選んだ往復すべてを元にする。
        """
        sources = tuple(one.id for one in kept)
        gists = tuple(Gist(text=text, made_at=dream.at, sources=sources) for text in texts)
        for gist in gists:
            self._memories.record_gist(dweller_id, dream.id, gist)
        return gists
