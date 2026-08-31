"""測るときと、記録から評価セットの下書きを作るときに扱うもの。

言葉は docs/150_system/用語集.md を正典とする。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import final

from yadori.domain.evaluation.failures import CannotMeasure


@final
@dataclass(frozen=True)
class Exchange:
    """評価セットで、あらかじめ覚えさせる一往復。名前で件から指す。"""

    name: str
    utterance: str
    reply: str


@final
@dataclass(frozen=True)
class Case:
    """一つの発話と、そのとき出るべきやりとり・出てはいけないやりとり。

    確認済みかどうかは、下書きから作った件だけが持つ。欄が無ければ（None）
    人が書いた件であり、確認済みとして扱う。語の重なりの度合いは、期待ごとに
    人が言い換えの件を見つける手掛かりで、測る側は読まない。
    """

    name: str
    utterance: str
    expected: tuple[str, ...]
    forbidden: tuple[str, ...]
    confirmed: bool | None = None
    overlap: tuple[tuple[str, float], ...] = ()

    @property
    def unconfirmed(self) -> bool:
        return self.confirmed is False


@final
@dataclass(frozen=True)
class RecallEval:
    """評価セット。覚えさせるやりとりと、測る件を持つ。"""

    within: int
    exchanges: tuple[Exchange, ...]
    cases: tuple[Case, ...]

    def verify_pointing(self) -> None:
        """指す先が揃い、期待と禁止が重ならず、名前が重なっていないことを確かめる。

        測る側と下書きを書く側の両方がこれを通す。欠けたまま測ると、順位が
        変わった理由が条件の変更なのか記憶の欠落なのか分からなくなる。
        """
        self._verify_unique_names()
        known = {exchange.name for exchange in self.exchanges}
        for case in self.cases:
            unknown = sorted((set(case.expected) | set(case.forbidden)) - known)
            if unknown:
                raise CannotMeasure(f"件「{case.name}」が無いやりとりを指している: {unknown}")
            both = sorted(set(case.expected) & set(case.forbidden))
            if both:
                raise CannotMeasure(
                    f"件「{case.name}」が同じやりとりを期待と禁止に指している: {both}"
                )

    def _verify_unique_names(self) -> None:
        for kind, names in (
            ("やりとり", [exchange.name for exchange in self.exchanges]),
            ("件", [case.name for case in self.cases]),
        ):
            repeated = sorted({name for name in names if names.count(name) > 1})
            if repeated:
                raise CannotMeasure(f"{kind}の名前が重なっている: {repeated}")

    @property
    def unconfirmed(self) -> int:
        return sum(1 for case in self.cases if case.unconfirmed)


@final
@dataclass(frozen=True)
class Ranked:
    """あるやりとりが、探した記憶の何番目に出たか。

    出なかった場合は順位も近さも持たない。
    """

    name: str
    rank: int | None
    relevance: float | None

    def within(self, limit: int) -> bool:
        return self.rank is not None and self.rank <= limit


@final
@dataclass(frozen=True)
class Outcome:
    """一件を測った結果。

    期待するやりとりが直近として渡っていた件は測れない。意味で探す側に現れ
    ないためである。満たさなかった件と混ぜると、直近の往復数を狭めただけで
    良くなったように見える。
    """

    case: str
    expected: tuple[Ranked, ...]
    forbidden: tuple[Ranked, ...]
    in_recent: tuple[str, ...]

    @property
    def measurable(self) -> bool:
        return not self.in_recent

    def met(self, within: int) -> bool:
        """期待したやりとりのどれかが順位に入り、出てはいけないものが出ていない。

        期待を複数書けるのは、正しいと言えるやりとりが複数あるためである。
        すべて出ることを求めると、正しい別の候補を出したときに外れになる。
        """
        return (
            self.measurable
            and (not self.expected or any(one.within(within) for one in self.expected))
            and not any(one.rank is not None for one in self.forbidden)
        )


@final
@dataclass(frozen=True)
class Measurement:
    """全件の結果。要約はここから求め、別に持たない。"""

    within: int
    outcomes: tuple[Outcome, ...]

    @property
    def total(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.measurable)

    @property
    def unmeasurable(self) -> int:
        return sum(1 for outcome in self.outcomes if not outcome.measurable)

    @property
    def met(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.met(self.within))

    @property
    def intruded(self) -> int:
        """出てはいけないやりとりが出た件の数。"""
        return sum(
            1 for outcome in self.outcomes if any(one.rank is not None for one in outcome.forbidden)
        )


@final
@dataclass(frozen=True)
class Shifted:
    """一件が、二つの測定の間でどう動いたか。"""

    case: str
    before: Outcome
    after: Outcome


@final
@dataclass(frozen=True)
class Difference:
    """二つの測定の差。変わらなかった件は持たない。"""

    better: tuple[Shifted, ...]
    worse: tuple[Shifted, ...]


# 記録から下書きを作るときに扱うもの。

# 中身のある語を持たない短い発話と、指す語だけの発話を除くための規則。
_POINTING_WORDS = re.compile(r"それ|あれ|これ|どれ|そこ|あそこ|ここ|どこ|さっき|前の|やつ|の件")
# 直前への同意、承諾、指示だけの言い回し。発話全体がこれらの並びだけなら話題を持たない。
_ACK_ONLY = re.compile(
    "^(?:"
    + "|".join(
        [
            "いいよ|いいね|いいじゃん|いいと思う|いいんじゃない|そうだね|そうしよう|そうする|そうそう",
            "そうね|やろう|進もう|進めよう|進めて|続けて|やって|なおそう|直そう|お願い|頼む|任せる",
            "マージして|マージしよう|マージしていい|了解|おっけ|オッケー|OK|ok|うん|はい|ありがとう",
            "次に行こう|次に行くか|次へ|それで|じゃあ|だね|かな|よ|ね|か|な|う|だ|です|ます|でしょう|ください|します|する",
        ]
    )
    + ")+$"
)
_NOT_WORDS = re.compile(r"[\s、。，．,.!?！？「」『』（）()\[\]・…〜~\-—]")
_SUBSTANCE_AT_LEAST = 8
# これより長い発話は、話した言葉ではなく貼り付けた資料（ログ、コード、文書の写し）とみなす。
_SPOKEN_AT_MOST = 400


@final
@dataclass(frozen=True)
class Recorded:
    """記録の一往復。対話する道具の記録から取り出した、発話と返事と時刻と作業場所。

    宿りの記憶（episode）ではなく、評価セットの材料である。中身があるかは自分で
    答える。
    """

    session: str
    workspace: str
    at: datetime
    utterance: str
    reply: str

    def has_substance(self) -> bool:
        """短い相槌や指す語だけの発話ではないか。

        指す語を取り除いても、まだ語として読める長さが残るものを中身があるとする。
        指す語だけの発話は意味で探しても出ないため、件にすると必ず満たさない。
        長すぎる発話は貼り付けた資料であり、話した言葉ではないので除く。
        """
        if len(self.utterance) > _SPOKEN_AT_MOST:
            return False
        squeezed = _NOT_WORDS.sub("", self.utterance)
        if _ACK_ONLY.match(squeezed):
            return False
        return len(_POINTING_WORDS.sub("", squeezed)) >= _SUBSTANCE_AT_LEAST


@final
@dataclass(frozen=True)
class Asking:
    """判定への問い。後の発話と、思い出す手順が引いた前の発話の候補。判定に渡るのはこれだけ。"""

    utterance: str
    candidates: tuple[str, ...]


@final
@dataclass(frozen=True)
class Pair:
    """組。判定が返す、後の発話と、それが指す前の発話の一対。

    問いの並びの番号（later）と、その問いの候補の並びの番号（earlier）で指す。
    """

    later: int
    earlier: int


@final
class Overlap:
    """語の重なりの度合い。隣り合う文字二つの並びをどれだけ共有するか。

    人が言い換えの件を見つける手掛かりであり、判定にも測る側にも使わない。
    索引を作る文字の埋め込みは差し替えの対象なので、ここでは縛らない。
    """

    def between(self, left: str, right: str) -> float:
        a, b = self._bigrams(left), self._bigrams(right)
        if not a or not b:
            return 0.0
        return round(len(a & b) / len(a | b), 2)

    def _bigrams(self, text: str) -> set[str]:
        squeezed = _NOT_WORDS.sub("", text)
        return {squeezed[i : i + 2] for i in range(len(squeezed) - 1)}


@final
@dataclass(frozen=True)
class Draft:
    """下書き。記録から作った評価セットと、何を取り出したかの数。

    下書きの件はすべて確認前なので、確認が要る数は件の数と同じで別に持たない。
    """

    recall_eval: RecallEval
    sessions: int
    spoken: int
    skipped_files: int

    @property
    def exchanges(self) -> int:
        return len(self.recall_eval.exchanges)

    @property
    def cases(self) -> int:
        return len(self.recall_eval.cases)
