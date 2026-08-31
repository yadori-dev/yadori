"""記録から評価セットの下書きを作る手順。

判定の口、読み手、書き手を受け取り、埋め込みの口は受け取らない。持たなければ
使えないため、測られる側の埋め込みで候補を選んでいないことが構造で保証される。
書くのは最後の一段だけで、読み取りと判定の途中で失敗しても何も書かれない。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import final

from yadori.domain.evaluation import (
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
from yadori.domain.memory import HowToRecall

# リポジトリの評価セットと同じ。何位までに入れば満たしたとするか。
WITHIN = 3


@final
class Drafting:
    def __init__(
        self,
        readers: Sequence[Records],
        judge: Judge,
        writer: DraftWriter,
        how: HowToRecall | None = None,
    ) -> None:
        self._readers: tuple[Records, ...] = tuple(readers)
        self._judge: Judge = judge
        self._writer: DraftWriter = writer
        self._how: HowToRecall = how or HowToRecall()
        self._overlap: Overlap = Overlap()

    def run(self, places: Sequence[Path], out: Path) -> Draft:
        """記録から下書きを作って書く。

        - 記録を読む（形式ごとの読み手。読めないファイルは飛ばして数える）
        - 中身のある一往復に絞り、同じ文言を最初の一つにする（この数を中身のある発話とする）
        - 作業場所ごとに判定し、組を集める
        - 組を解いて評価セットに組む
        - 指す先が揃っていることを確かめてから書く
        """
        recorded, skipped = self._recorded(places)
        spoken = self._substantial(recorded)
        unique = self._deduplicated(spoken)
        pairs = self._judged(unique)
        recall_eval = self._resolved(unique, pairs)
        if not recall_eval.cases:
            raise CannotDraft("後の発話が前の話題を指す件が一つも出ませんでした")
        recall_eval.verify_pointing()
        self._writer.write(out, recall_eval)
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

    # 判定する

    def _judged(self, recorded: Sequence[Recorded]) -> list[Pair]:
        """作業場所ごとに一度判定し、組の番号を全体の並びの番号へ直す。"""
        pairs: list[Pair] = []
        for indexes in self._by_workspace(recorded):
            utterances = [recorded[index].utterance for index in indexes]
            for pair in self._judge.pairs(utterances):
                whole = Pair(later=indexes[pair.later], earlier=indexes[pair.earlier])
                if not self._within_recent_of_session(recorded, whole):
                    pairs.append(whole)
        return pairs

    def _within_recent_of_session(self, recorded: Sequence[Recorded], pair: Pair) -> bool:
        """同じセッションで直近往復数以内の前の発話を指しているか。

        実際の会話ではその範囲は直近として渡り、意味で探す側には現れない。件に
        しても測れず、直前への返しばかりが件に混ざる。
        """
        later, earlier = recorded[pair.later], recorded[pair.earlier]
        if later.session != earlier.session:
            return False
        between = sum(
            1 for one in recorded[pair.earlier + 1 : pair.later] if one.session == later.session
        )
        return between < self._how.recent_turns

    def _by_workspace(self, recorded: Sequence[Recorded]) -> list[list[int]]:
        grouped: dict[str, list[int]] = {}
        for index, one in enumerate(recorded):
            grouped.setdefault(one.workspace, []).append(index)
        return list(grouped.values())

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
