"""記録から評価セットの下書きを作る手順と、既にある下書きへ増えた分だけを足す手順。

候補は宿りの思い出す手順（会話と同じ `Conversation.recall`）で引く。作業場所ごとに
使い捨ての記憶を組み、発話を時刻の順に、まず思い出して候補を得てから覚えさせる。
判定に渡すのは後の発話とその候補だけで、作業場所の発話を丸ごと渡さない。
宿りは必要な記憶だけを渡すための道具であり、その周辺の道具も同じ前提で作る。

新しく作るのは「前回が空の追記」であり、組を解く部品は一つしか持たない。
書くのは最後の一段だけで、読み取りと判定の途中で失敗しても何も書かれない。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import final

from yadori.domain.evaluation import (
    Added,
    Appended,
    Asking,
    BrokenRecord,
    CannotDraft,
    CannotMeasure,
    Case,
    Covered,
    Draft,
    Drafts,
    DrawnWith,
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
from yadori.usecase.evaluation.ticking import Ticking

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
@dataclass(frozen=True)
class _Placed:
    """解く並びの一つ。前回のやりとりは名前付き、新しい発話は名前無し。

    作業場所と時刻は候補を引く記憶へ入れる順を決めるためで、前回のやりとりは記録の
    読み直しから引く。記録に見つからない前回のやりとりは持たず、記憶に入らない。
    """

    name: str | None
    utterance: str
    reply: str
    workspace: str | None
    at: datetime | None

    @property
    def is_new(self) -> bool:
        return self.name is None


@final
@dataclass(frozen=True)
class _Previous:
    """前回の下書き。新しく作るときは空で、番号は 0 から始まる。"""

    exchanges: tuple[Exchange, ...]
    cases: tuple[Case, ...]
    last_exchange: int
    last_case: int

    @property
    def utterances(self) -> frozenset[str]:
        return frozenset(one.utterance for one in self.exchanges) | frozenset(
            one.utterance for one in self.cases
        )


_EMPTY = _Previous(exchanges=(), cases=(), last_exchange=0, last_case=0)


@final
@dataclass(frozen=True)
class _Resolved:
    """組を解いた結果。合わせた評価セットと、今回足した分と、使った名前の最後の番号。

    番号は下書きの中の名前からは求めない。人が名前を変えても消しても使い回さない。
    """

    recall_eval: RecallEval
    added: Added
    last_exchange: int
    last_case: int


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
        self._overlap: Overlap = Overlap()

    def drawn_with(self) -> DrawnWith:
        """何で候補を引き、何で判定するか。画面と下書きの前回の範囲に残す。"""
        return DrawnWith(
            provenance=self._embeddings.provenance, how=self._how, judge=self._judge.name
        )

    def run(self, places: Sequence[Path], out: Path) -> Draft:
        """記録から下書きを作って書く。

        - 記録を読む（形式ごとの読み手。読めないファイルは飛ばして残す）
        - 中身のある一往復に絞り、同じ文言を最初の一つにする（この数を中身のある発話とする）
        - 作業場所ごとに、思い出して候補を引きながら覚えさせる
        - 候補のある発話だけを判定に渡し、組を集める
        - 組を解いて評価セットに組む
        - 指す先が揃っていることを確かめてから書く
        """
        recorded, skipped = self._recorded(places)
        incoming = self._incoming([one for _, one in recorded], _EMPTY)
        resolved, _, _ = self._drawn(_EMPTY, recorded, incoming)
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
        previous = _Previous(
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

    def _incoming(self, recorded: Sequence[Recorded], previous: _Previous) -> list[Recorded]:
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
        previous: _Previous,
        recorded: Sequence[tuple[Path, Recorded]],
        incoming: Sequence[Recorded],
    ) -> tuple[_Resolved, int, int]:
        placed = self._placed(previous, [one for _, one in recorded], incoming)
        askings = self._asked(placed)
        asked = len(askings)
        unasked = sum(1 for one in placed if one.is_new) - asked
        pairs = self._judged(placed, askings)
        return self._resolved(previous, placed, pairs), asked, unasked

    def _placed(
        self, previous: _Previous, recorded: Sequence[Recorded], incoming: Sequence[Recorded]
    ) -> list[_Placed]:
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
            _Placed(
                name=None,
                utterance=one.utterance,
                reply=one.reply,
                workspace=one.workspace,
                at=one.at,
            )
            for one in incoming
        )
        return placed

    def _placed_previous(self, exchange: Exchange, found: Recorded | None) -> _Placed:
        return _Placed(
            name=exchange.name,
            utterance=exchange.utterance,
            reply=exchange.reply,
            workspace=None if found is None else found.workspace,
            at=None if found is None else found.at,
        )

    def _asked(self, placed: Sequence[_Placed]) -> list[tuple[int, Asking]]:
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

    def _by_workspace(self, placed: Sequence[_Placed]) -> list[list[int]]:
        """作業場所ごとの、時刻の順の番号。記録に見つからなかった前回のやりとりは入らない。"""
        grouped: dict[str, list[int]] = {}
        for index, one in enumerate(placed):
            if one.workspace is not None:
                grouped.setdefault(one.workspace, []).append(index)
        return [
            sorted(indexes, key=lambda index: self._at_of(placed[index]))
            for indexes in grouped.values()
        ]

    def _at_of(self, one: _Placed) -> datetime:
        if one.at is None:
            raise CannotDraft("時刻の無い発話を記憶に入れようとしました")
        return one.at

    def _judged(
        self, placed: Sequence[_Placed], askings: Sequence[tuple[int, Asking]]
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

    def _resolved(
        self, previous: _Previous, placed: Sequence[_Placed], pairs: Sequence[Pair]
    ) -> _Resolved:
        """判定の結果を、測れる評価セットの形へ解く。

        - 同じ後の発話の組は、期待を複数持つ一問にまとめる
        - 問と期待の両方になる発話は期待に残し、問にしない
        - 問を外した後に残る並びの末尾の直近に入る期待は外す（数える並びは戻す前のもの）
        - 期待が一つも残らない発話は問にせず、覚えさせる側に戻す
        - 名前は前回の最後の番号の次から付け、合わせた評価セットで指す先を確かめる
        """
        expected_of = self._kept_as_expected(self._merged(pairs))
        exchange_indexes = [index for index in range(len(placed)) if index not in expected_of]
        recent = (
            set(exchange_indexes[-self._how.recent_turns :])
            if self._how.recent_turns
            else set[int]()
        )
        case_of: dict[int, list[int]] = {}
        for later in sorted(expected_of):
            earliers = [earlier for earlier in expected_of[later] if earlier not in recent]
            if earliers:
                case_of[later] = earliers
        exchange_indexes = [index for index in range(len(placed)) if index not in case_of]
        names = self._named(placed, exchange_indexes, previous.last_exchange)
        new_exchanges = tuple(
            Exchange(
                name=names[index], utterance=placed[index].utterance, reply=placed[index].reply
            )
            for index in exchange_indexes
            if placed[index].is_new
        )
        new_cases = tuple(
            self._case(placed, later, earliers, names, previous.last_case + number)
            for number, (later, earliers) in enumerate(sorted(case_of.items()), start=1)
        )
        recall_eval = RecallEval(
            within=WITHIN,
            exchanges=previous.exchanges + new_exchanges,
            cases=previous.cases + new_cases,
        )
        try:
            recall_eval.verify_pointing()
        except CannotMeasure as broken:
            raise CannotDraft(f"下書きが測れる形になりません: {broken}") from broken
        return _Resolved(
            recall_eval=recall_eval,
            added=Added(exchanges=new_exchanges, cases=new_cases),
            last_exchange=previous.last_exchange + len(new_exchanges),
            last_case=previous.last_case + len(new_cases),
        )

    def _named(
        self, placed: Sequence[_Placed], exchange_indexes: Sequence[int], last_exchange: int
    ) -> dict[int, str]:
        names: dict[int, str] = {}
        number = last_exchange
        for index in exchange_indexes:
            name = placed[index].name
            if name is None:
                number += 1
                name = f"e{number:03d}"
            names[index] = name
        return names

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
        placed: Sequence[_Placed],
        later: int,
        earliers: Sequence[int],
        names: dict[int, str],
        number: int,
    ) -> Case:
        utterance = placed[later].utterance
        return Case(
            name=f"c{number:03d}",
            utterance=utterance,
            expected=tuple(names[earlier] for earlier in earliers),
            forbidden=(),
            confirmed=False,
            overlap=tuple(
                (names[earlier], self._overlap.between(utterance, placed[earlier].utterance))
                for earlier in earliers
            ),
        )

    # 前回の範囲

    def _covered(
        self,
        places: Sequence[Path],
        recorded: Sequence[tuple[Path, Recorded]],
        skipped: tuple[str, ...],
        resolved: _Resolved,
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
