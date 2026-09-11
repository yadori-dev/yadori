"""記録から評価セットの下書きを作る手順と、既にある下書きへ増えた分だけを足す手順。

候補は宿りの思い出す手順（会話と同じ `Conversation.recall`）で引く。作業場所ごとに
使い捨ての記憶を組み、発話を時刻の順に、まず思い出して候補を得てから覚えさせる。
判定に渡すのは後の発話とその候補だけで、作業場所の発話を丸ごと渡さない。
宿りは必要な記憶だけを渡すための道具であり、その周辺の道具も同じ前提で作る。

新しく作るのは「前回が空の追記」であり、組を解く部品（`Resolving`）は一つしか持たない。
書くのは最後の一段だけで、読み取りと判定の途中で失敗しても何も書かれない。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import final

from yadori.domain.evaluation import (
    Appended,
    Asking,
    BrokenRecord,
    CannotDraft,
    Covered,
    Draft,
    Drafts,
    DrawnWith,
    Exchange,
    Judge,
    Pair,
    Recorded,
    Records,
)
from yadori.domain.memory import Dweller, Embeddings, HowToRecall, Memories
from yadori.usecase.conversation import Conversation
from yadori.usecase.evaluation.resolve import (
    EMPTY_PREVIOUS,
    Placed,
    Previous,
    Resolved,
    Resolving,
    Where,
)
from yadori.usecase.evaluation.ticking import Ticking

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
        drafts: Drafts,
        embeddings: Embeddings,
        fresh_memories: Callable[[], Memories],
        how: HowToRecall = DRAFT_HOW,
    ) -> None:
        self._readers: tuple[Records, ...] = tuple(readers)
        self._judge: Judge = judge
        self._drafts: Drafts = drafts
        self._embeddings: Embeddings = embeddings
        self._fresh_memories: Callable[[], Memories] = fresh_memories
        self._how: HowToRecall = how
        self._resolving: Resolving = Resolving(how)

    def drawn_with(self) -> DrawnWith:
        """何で候補を引き、何で判定するか。画面と下書きの前回の範囲に残す。"""
        return DrawnWith(
            provenance=self._embeddings.provenance, how=self._how, judge=self._judge.name
        )

    def run(self, places: Sequence[Path], out: Path) -> Draft:
        """記録から下書きを作って書く。

        - 書ける先かを確かめる（判定を全部走らせた後に断るのは遅い）
        - 記録を読む（形式ごとの読み手。読めないファイルは飛ばして残す）
        - 中身のある一往復に絞り、同じ文言を最初の一つにする（この数を中身のある発話とする）
        - 作業場所ごとに、思い出して候補を引きながら覚えさせる
        - 候補のある発話だけを判定に渡し、組を集める
        - 組を解いて評価セットに組む
        - 指す先が揃っていることを確かめてから書く
        """
        self._drafts.verify_writable(out)
        recorded, skipped = self._recorded(places)
        incoming = self._incoming([one for _, one in recorded], EMPTY_PREVIOUS)
        resolved, _, _ = self._drawn(EMPTY_PREVIOUS, recorded, incoming)
        if not resolved.recall_eval.cases:
            raise CannotDraft("後の発話が前の話題を指す問が一つも出ませんでした")
        covered = self._covered(places, recorded, skipped, resolved)
        self._drafts.write(out, resolved.recall_eval, covered)
        return Draft(
            recall_eval=resolved.recall_eval,
            sessions=len({one.session for _, one in recorded}),
            spoken=len(incoming),
            skipped=skipped,
        )

    def append(self, places: Sequence[Path], out: Path) -> Appended:
        """既にある下書きへ、前回の範囲より後に増えた記録の分だけを足す。

        - 下書きを読み、引き方とディレクトリが今と同じかを確かめる（違えば記録を読まずに断る）
        - 記録を全部読み直し、前回の時刻より後、または前回飛ばしたファイルの一往復を
          新しい発話とする
        - 前回の覚えさせる発話と新しい発話だけで記憶を組み、新しい発話だけを判定に渡す
        - 合わせた並びで解き、名前は前回の最後の番号から続ける
        - 合わせた評価セットの指す先が揃っていることを確かめてから、足す分だけを末尾に足す
        """
        previous_eval, before = self._drafts.read(out)
        self._refuse_if_differs(before, places)
        previous = Previous(
            exchanges=previous_eval.exchanges,
            cases=previous_eval.cases,
            last_exchange=before.last_exchange,
            last_case=before.last_case,
        )
        recorded, skipped = self._recorded(places)
        arrived = [
            one for path, one in recorded if one.at > before.until or str(path) in before.skipped
        ]
        incoming = self._incoming(arrived, previous)
        resolved, asked, unasked = self._drawn(previous, recorded, incoming)
        covered = self._covered(places, recorded, skipped, resolved)
        self._drafts.append(out, resolved.added, covered)
        return Appended(
            covered=before,
            previous_exchanges=len(previous.exchanges),
            previous_cases=len(previous.cases),
            new_sessions=len({one.session for one in arrived}),
            incoming=len(incoming),
            skipped=skipped,
            asked=asked,
            unasked=unasked,
            added_exchanges=len(resolved.added.exchanges),
            added_cases=len(resolved.added.cases),
            notice=before.drawn_with.tool_version_changed(self.drawn_with()),
        )

    def _refuse_if_differs(self, before: Covered, places: Sequence[Path]) -> None:
        differing = [
            reason
            for reason in (
                before.drawn_with.differs_from(self.drawn_with()),
                before.places_differ_from(self._place_names(places)),
            )
            if reason is not None
        ]
        if differing:
            raise CannotDraft(
                "前回と違うものがあるので追記できません: "
                + "、".join(differing)
                + "。混ぜると何で引いた下書きか読めなくなるので、新しいファイルへ作り直してください"
            )

    # 記録を読む

    def _recorded(
        self, places: Sequence[Path]
    ) -> tuple[list[tuple[Path, Recorded]], tuple[str, ...]]:
        recorded: list[tuple[Path, Recorded]] = []
        skipped: list[str] = []
        for place in places:
            if place.is_file():
                raise CannotDraft(
                    f"指した先 {place} はファイルです。記録のディレクトリを指してください"
                )
            if not place.is_dir():
                raise CannotDraft(f"記録のディレクトリ {place} がありません")
            for path in sorted(place.rglob("*.jsonl")):
                read = self._one_file(path)
                if read is None:
                    skipped.append(str(path.resolve()))
                else:
                    recorded.extend((path.resolve(), one) for one in read)
        if not recorded:
            raise CannotDraft("記録から一往復を一件も取り出せませんでした")
        return sorted(recorded, key=lambda pair: pair[1].at), tuple(skipped)

    def _one_file(self, path: Path) -> tuple[Recorded, ...] | None:
        """どの読み手も自分の形式と答えないファイルと、途中で読めないファイルは飛ばす。"""
        for reader in self._readers:
            if reader.claims(path):
                try:
                    return reader.read(path)
                except BrokenRecord:
                    return None
        return None

    def _incoming(self, recorded: Sequence[Recorded], previous: Previous) -> list[Recorded]:
        """新しい発話。中身のあるものに絞り、同じ文言は最初の一つにし、前回の下書きにある文言を除く。

        測る側は文言で照合するため、同じ文言が二つあると指す先が定まらない。
        """
        seen: set[str] = set(previous.utterances)
        kept: list[Recorded] = []
        for one in recorded:
            if one.has_substance() and one.utterance not in seen:
                seen.add(one.utterance)
                kept.append(one)
        return kept

    def _place_names(self, places: Sequence[Path]) -> tuple[str, ...]:
        return tuple(str(place.resolve()) for place in places)

    # 候補を引き、判定し、解く

    def _drawn(
        self,
        previous: Previous,
        recorded: Sequence[tuple[Path, Recorded]],
        incoming: Sequence[Recorded],
    ) -> tuple[Resolved, int, int]:
        placed = self._placed(previous, [one for _, one in recorded], incoming)
        askings = self._asked(placed)
        asked = len(askings)
        unasked = sum(1 for one in placed if one.is_new) - asked
        pairs = self._judged(placed, askings)
        return self._resolving.resolve(previous, placed, pairs), asked, unasked

    def _placed(
        self, previous: Previous, recorded: Sequence[Recorded], incoming: Sequence[Recorded]
    ) -> list[Placed]:
        """解く並び。前回のやりとり（下書きの順）の後に、新しい発話を時刻の順で置く。

        前回のやりとりの作業場所と時刻は、記録の同じ文言の一往復（最初の一つ）から引く。
        返事は下書きのものを使う。測るときも下書きの返事で覚えさせるためである。
        """
        first_of: dict[str, Recorded] = {}
        for one in recorded:
            _ = first_of.setdefault(one.utterance, one)
        placed = [
            self._placed_previous(exchange, first_of.get(exchange.utterance))
            for exchange in previous.exchanges
        ]
        placed.extend(
            Placed(
                name=None,
                utterance=one.utterance,
                reply=one.reply,
                where=Where(one.workspace, one.at),
            )
            for one in incoming
        )
        return placed

    def _placed_previous(self, exchange: Exchange, found: Recorded | None) -> Placed:
        return Placed(
            name=exchange.name,
            utterance=exchange.utterance,
            reply=exchange.reply,
            where=None if found is None else Where(found.workspace, found.at),
        )

    def _asked(self, placed: Sequence[Placed]) -> list[tuple[int, Asking]]:
        """作業場所ごとに使い捨ての記憶を組み、時刻の順に、思い出してから覚える。

        問いにするのは候補のある新しい発話だけ。前回のやりとりは覚えさせるだけで問いにしない。
        並びが時刻の順なので、前回飛ばしたファイル由来の古い発話も自分より前の発話からしか引かない。
        """
        askings: list[tuple[int, Asking]] = []
        for indexes in self._by_workspace(placed):
            conversation = self._fresh_conversation()
            for index in indexes:
                one = placed[index]
                if one.is_new:
                    found = conversation.recall(DRAFTED.id, one.utterance).found
                    if found:
                        candidates = tuple(hit.episode.utterance for hit in found)
                        askings.append(
                            (index, Asking(utterance=one.utterance, candidates=candidates))
                        )
                _ = conversation.remember(DRAFTED.id, one.utterance, one.reply)
        return askings

    def _fresh_conversation(self) -> Conversation:
        memories = self._fresh_memories()
        memories.settle(DRAFTED)
        _ = memories.write_identity(DRAFTED.id, NAME_DECLARED)
        return Conversation(memories, self._embeddings, Ticking(), self._how)

    def _by_workspace(self, placed: Sequence[Placed]) -> list[list[int]]:
        """作業場所ごとの、時刻の順の番号。記録に見つからなかった前回のやりとりは入らない。"""
        grouped: dict[str, list[tuple[datetime, int]]] = {}
        for index, one in enumerate(placed):
            if one.where is not None:
                grouped.setdefault(one.where.workspace, []).append((one.where.at, index))
        return [[index for _, index in sorted(indexes)] for indexes in grouped.values()]

    def _judged(
        self, placed: Sequence[Placed], askings: Sequence[tuple[int, Asking]]
    ) -> list[Pair]:
        """問いをいくつかずつ判定に渡し、組の番号を全体の並びの番号へ直す。"""
        position = {one.utterance: index for index, one in enumerate(placed)}
        pairs: list[Pair] = []
        for start in range(0, len(askings), ASK_AT_MOST):
            batch = askings[start : start + ASK_AT_MOST]
            for pair in self._judge.pairs([asking for _, asking in batch]):
                later_index, asking = batch[pair.later]
                pairs.append(
                    Pair(later=later_index, earlier=position[asking.candidates[pair.earlier]])
                )
        return pairs

    # 前回の範囲

    def _covered(
        self,
        places: Sequence[Path],
        recorded: Sequence[tuple[Path, Recorded]],
        skipped: tuple[str, ...],
        resolved: Resolved,
    ) -> Covered:
        """次の追記が読む印。今回読み直した全記録から求める。

        引き方は今のものを書く。前回と違えば既に断っているので、版だけが進んだことになる。
        """
        return Covered(
            until=max(one.at for _, one in recorded),
            places=self._place_names(places),
            skipped=skipped,
            sessions=len({one.session for _, one in recorded}),
            last_exchange=resolved.last_exchange,
            last_case=resolved.last_case,
            drawn_with=self.drawn_with(),
        )
