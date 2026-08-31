"""記録から評価セットの下書きを作る手順。

候補は宿りの思い出す手順（会話と同じ `Conversation.recall`）で引く。作業場所ごとに
使い捨ての記憶を組み、記録の一往復を時刻の順に、まず思い出して候補を得てから
覚えさせる。判定に渡すのは後の発話とその候補だけで、作業場所の発話を丸ごと渡さない。
宿りは必要な記憶だけを渡すための道具であり、その周辺の道具も同じ前提で作る。

書くのは最後の一段だけで、読み取りと判定の途中で失敗しても何も書かれない。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import final

from yadori.domain.evaluation import (
    Asking,
    BrokenRecord,
    CannotDraft,
    Case,
    Draft,
    DraftWriter,
    Exchange,
    Judge,
    Overlap,
    Pair,
    RecallEval,
    Recorded,
    Records,
)
from yadori.domain.memory import Dweller, Embeddings, HowToRecall, Memories
from yadori.usecase.conversation import Conversation

# リポジトリの評価セットと同じ。何位までに入れば満たしたとするか。
WITHIN = 3
# 下書き用の思い出し方。普段より下限を低く、件数を多く取り、候補を広めに引く。
# 直近の往復数は普段と同じにし、直近が渡す範囲の前の発話は候補に入らない。
DRAFT_HOW = HowToRecall(recent_turns=6, found_limit=10, relevance_floor=0.30)
# 一度の判定に渡す問いの数。候補が問いごとに最大十件なので、渡す量はこれで抑える。
ASK_AT_MOST = 10
DRAFTED = Dweller(id="drafted", owner="下書きのためだけの持ち主", name="下書き", nickname="下書き")
NAME_DECLARED = "下書きのためだけの名乗り。応対は作らない。"


@final
class Drafting:
    def __init__(
        self,
        readers: Sequence[Records],
        judge: Judge,
        writer: DraftWriter,
        embeddings: Embeddings,
        fresh_memories: Callable[[], Memories],
        how: HowToRecall = DRAFT_HOW,
    ) -> None:
        self._readers: tuple[Records, ...] = tuple(readers)
        self._judge: Judge = judge
        self._writer: DraftWriter = writer
        self._embeddings: Embeddings = embeddings
        self._fresh_memories: Callable[[], Memories] = fresh_memories
        self._how: HowToRecall = how
        self._overlap: Overlap = Overlap()

    def drawn_with(self) -> str:
        """何で候補を引くか。画面と下書きの冒頭に残す。"""
        return (
            f"埋め込み: {self._embeddings.name} / 条件: 直近{self._how.recent_turns}往復・"
            + f"候補{self._how.found_limit}件・下限{self._how.relevance_floor}"
        )

    def run(self, places: Sequence[Path], out: Path) -> Draft:
        """記録から下書きを作って書く。

        - 記録を読む（形式ごとの読み手。読めないファイルは飛ばして数える）
        - 中身のある一往復に絞り、同じ文言を最初の一つにする（この数を中身のある発話とする）
        - 作業場所ごとに、思い出して候補を引きながら覚えさせる
        - 候補のある発話だけを判定に渡し、組を集める
        - 組を解いて評価セットに組む
        - 指す先が揃っていることを確かめてから書く
        """
        recorded, skipped = self._recorded(places)
        unique = self._deduplicated(self._substantial(recorded))
        askings = self._asked(unique)
        pairs = self._judged(unique, askings)
        recall_eval = self._resolved(unique, pairs)
        if not recall_eval.cases:
            raise CannotDraft("後の発話が前の話題を指す件が一つも出ませんでした")
        recall_eval.verify_pointing()
        self._writer.write(out, recall_eval, self.drawn_with())
        return Draft(
            recall_eval=recall_eval,
            sessions=len({one.session for one in recorded}),
            spoken=len(unique),
            skipped_files=skipped,
        )

    # 記録を読む

    def _recorded(self, places: Sequence[Path]) -> tuple[list[Recorded], int]:
        recorded: list[Recorded] = []
        skipped = 0
        for place in places:
            if place.is_file():
                raise CannotDraft(
                    f"記録の置き場 {place} はファイルです。ディレクトリを指してください"
                )
            if not place.is_dir():
                raise CannotDraft(f"記録の置き場 {place} がありません")
            for path in sorted(place.rglob("*.jsonl")):
                read, skipped_one = self._one_file(path)
                recorded.extend(read)
                skipped += skipped_one
        if not recorded:
            raise CannotDraft("記録から一往復を一件も取り出せませんでした")
        return sorted(recorded, key=lambda one: one.at), skipped

    def _one_file(self, path: Path) -> tuple[tuple[Recorded, ...], int]:
        """どの読み手も自分の形式と答えないファイルと、途中で読めないファイルは飛ばす。"""
        for reader in self._readers:
            if reader.claims(path):
                try:
                    return reader.read(path), 0
                except BrokenRecord:
                    return (), 1
        return (), 1

    def _substantial(self, recorded: Sequence[Recorded]) -> list[Recorded]:
        return [one for one in recorded if one.has_substance()]

    def _deduplicated(self, recorded: Sequence[Recorded]) -> list[Recorded]:
        """同じ文言は置き場全体で最初の一つだけを残す。測る側は文言で照合する。"""
        seen: set[str] = set()
        kept: list[Recorded] = []
        for one in recorded:
            if one.utterance not in seen:
                seen.add(one.utterance)
                kept.append(one)
        return kept

    # 候補を引く

    def _asked(self, recorded: Sequence[Recorded]) -> list[tuple[int, Asking]]:
        """作業場所ごとに使い捨ての記憶を組み、思い出してから覚える。候補のある発話だけを問いにする。"""
        askings: list[tuple[int, Asking]] = []
        for indexes in self._by_workspace(recorded):
            conversation = self._fresh_conversation()
            for index in indexes:
                one = recorded[index]
                found = conversation.recall(DRAFTED.id, one.utterance).found
                if found:
                    candidates = tuple(hit.episode.utterance for hit in found)
                    askings.append((index, Asking(utterance=one.utterance, candidates=candidates)))
                _ = conversation.remember(DRAFTED.id, one.utterance, one.reply)
        return askings

    def _fresh_conversation(self) -> Conversation:
        memories = self._fresh_memories()
        memories.settle(DRAFTED)
        _ = memories.write_identity(DRAFTED.id, NAME_DECLARED)
        return Conversation(memories, self._embeddings, _Ticking(), self._how)

    def _by_workspace(self, recorded: Sequence[Recorded]) -> list[list[int]]:
        grouped: dict[str, list[int]] = {}
        for index, one in enumerate(recorded):
            grouped.setdefault(one.workspace, []).append(index)
        return list(grouped.values())

    # 判定する

    def _judged(
        self, recorded: Sequence[Recorded], askings: Sequence[tuple[int, Asking]]
    ) -> list[Pair]:
        """問いをいくつかずつ判定に渡し、組の番号を全体の並びの番号へ直す。"""
        position = {one.utterance: index for index, one in enumerate(recorded)}
        pairs: list[Pair] = []
        for start in range(0, len(askings), ASK_AT_MOST):
            batch = askings[start : start + ASK_AT_MOST]
            for pair in self._judge.pairs([asking for _, asking in batch]):
                later_index, asking = batch[pair.later]
                pairs.append(
                    Pair(later=later_index, earlier=position[asking.candidates[pair.earlier]])
                )
        return pairs

    # 組を解く

    def _resolved(self, recorded: Sequence[Recorded], pairs: Sequence[Pair]) -> RecallEval:
        """判定の結果を、測れる評価セットの形へ解く。

        - 同じ後の発話の組は、期待を複数持つ一件にまとめる
        - 件と期待の両方になる発話は期待に残し、件にしない
        - 件を外した後に残る並びの末尾の直近に入る期待は外す
        - 期待が一つも残らない件は出さない
        """
        expected_of = self._merged(pairs)
        expected_of = self._kept_as_expected(expected_of)
        exchange_indexes = [index for index in range(len(recorded)) if index not in expected_of]
        recent = (
            set(exchange_indexes[-self._how.recent_turns :])
            if self._how.recent_turns
            else set[int]()
        )
        names = {index: f"e{place:03d}" for place, index in enumerate(exchange_indexes, start=1)}
        cases: list[Case] = []
        for later in sorted(expected_of):
            earliers = [earlier for earlier in expected_of[later] if earlier not in recent]
            if earliers:
                cases.append(self._case(recorded, later, earliers, names, len(cases) + 1))
        exchanges = tuple(
            Exchange(
                name=names[index], utterance=recorded[index].utterance, reply=recorded[index].reply
            )
            for index in exchange_indexes
        )
        return RecallEval(within=WITHIN, exchanges=exchanges, cases=tuple(cases))

    def _merged(self, pairs: Sequence[Pair]) -> dict[int, list[int]]:
        merged: dict[int, list[int]] = {}
        for pair in pairs:
            earliers = merged.setdefault(pair.later, [])
            if pair.earlier not in earliers:
                earliers.append(pair.earlier)
        return merged

    def _kept_as_expected(self, expected_of: dict[int, list[int]]) -> dict[int, list[int]]:
        pointed = {earlier for earliers in expected_of.values() for earlier in earliers}
        return {later: earliers for later, earliers in expected_of.items() if later not in pointed}

    def _case(
        self,
        recorded: Sequence[Recorded],
        later: int,
        earliers: Sequence[int],
        names: dict[int, str],
        number: int,
    ) -> Case:
        utterance = recorded[later].utterance
        return Case(
            name=f"c{number:03d}",
            utterance=utterance,
            expected=tuple(names[earlier] for earlier in earliers),
            forbidden=(),
            confirmed=False,
            overlap=tuple(
                (names[earlier], self._overlap.between(utterance, recorded[earlier].utterance))
                for earlier in earliers
            ),
        )


@final
class _Ticking:
    """使い捨ての記憶の時刻。結果を実際の時刻に左右させない。"""

    def __init__(self) -> None:
        self._at: datetime = datetime(2000, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        self._at += timedelta(minutes=1)
        return self._at
