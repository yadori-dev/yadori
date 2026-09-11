"""測るときと、記録から評価セットの下書きを作るときに扱うもの。

言葉は docs/150_system/用語集.md を正典とする。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import final

from yadori.domain.evaluation.failures import CannotMeasure
from yadori.domain.memory import HowToRecall, Provenance


@final
@dataclass(frozen=True)
class Exchange:
    """評価セットで、あらかじめ覚えさせる一往復。名前で問から指す。"""

    name: str
    utterance: str
    reply: str


@final
@dataclass(frozen=True)
class Case:
    """一つの発話と、そのとき出るべきやりとり・出てはいけないやりとり。

    確認済みかどうかは、下書きから作った問だけが持つ。欄が無ければ（None）
    人が書いた問であり、確認済みとして扱う。語の重なりの度合いは、期待ごとに
    人が言い換えの問を見つける手掛かりで、測る側は読まない。
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
    """評価セット。覚えさせるやりとりと、測る問を持つ。"""

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
                raise CannotMeasure(f"問「{case.name}」が無いやりとりを指している: {unknown}")
            both = sorted(set(case.expected) & set(case.forbidden))
            if both:
                raise CannotMeasure(
                    f"問「{case.name}」が同じやりとりを期待と禁止に指している: {both}"
                )

    def _verify_unique_names(self) -> None:
        for kind, names in (
            ("やりとり", [exchange.name for exchange in self.exchanges]),
            ("問", [case.name for case in self.cases]),
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
    """一問を測った結果。

    期待するやりとりが直近として渡っていた問は測れない。意味で探す側に現れ
    ないためである。満たさなかった問と混ぜると、直近の往復数を狭めただけで
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
    """全問の結果。要約はここから求め、別に持たない。"""

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
        """出てはいけないやりとりが出た問の数。"""
        return sum(
            1 for outcome in self.outcomes if any(one.rank is not None for one in outcome.forbidden)
        )


@final
@dataclass(frozen=True)
class Shifted:
    """一問が、二つの測定の間でどう動いたか。"""

    case: str
    before: Outcome
    after: Outcome


@final
@dataclass(frozen=True)
class Difference:
    """二つの測定の差。変わらなかった問は持たない。"""

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
        指す語だけの発話は意味で探しても出ないため、問にすると必ず満たさない。
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

    人が言い換えの問を見つける手掛かりであり、判定にも測る側にも使わない。
    インデックスを作る文字の埋め込みは差し替えの対象なので、ここでは縛らない。
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
class DrawnWith:
    """候補の引き方。何で候補を引き、何で判定したか。下書きの前回の範囲に残る。

    追記を断るのは、埋め込みの AIモデルか道具の名前か添え書き、思い出し方、判定の
    AIモデルが違うとき。道具の版だけの違いは断らず注意にする。版で断ると依存を上げるたびに
    確認済みの問が無駄になる。
    """

    provenance: Provenance
    how: HowToRecall
    judge: str

    def differs_from(self, now: DrawnWith) -> str | None:
        """前回（自分）から見て、今の引き方（now）と違うところ。同じなら無し。"""
        found: list[str] = []
        if self.provenance.ai_model != now.provenance.ai_model:
            found.append(
                f"埋め込みの AIモデル（前回 {self.provenance.ai_model or 'AIモデル無し'}、"
                + f"今回 {now.provenance.ai_model or 'AIモデル無し'}）"
            )
        if self.provenance.tool != now.provenance.tool:
            found.append(
                f"埋め込みを動かす道具（前回 {self.provenance.tool}、今回 {now.provenance.tool}）"
            )
        if self.provenance.prefixes != now.provenance.prefixes:
            # 添え書きが違えば数の並びの作り方が違い、候補の引き方も変わる。
            found.append(
                f"添え書き（前回 {self.provenance.prefixes_described}、"
                + f"今回 {now.provenance.prefixes_described}）"
            )
        if self.how != now.how:
            found.append(
                f"思い出し方（前回 {self._how_of(self.how)}、今回 {self._how_of(now.how)}）"
            )
        if self.judge != now.judge:
            found.append(f"判定の AIモデル（前回 {self.judge}、今回 {now.judge}）")
        return "、".join(found) if found else None

    def tool_version_changed(self, now: DrawnWith) -> str | None:
        """前回（自分）から見て、今の道具の版だけが違うときの注意。同じなら無し。"""
        if self.provenance.tool_version == now.provenance.tool_version:
            return None
        return (
            f"埋め込みを動かす道具の版が前回（{self.provenance.tool}-{self.provenance.tool_version}）"
            + f"と違います（今回 {now.provenance.tool}-{now.provenance.tool_version}）。"
            + "候補の引き方がわずかに変わり得ます"
        )

    @property
    def described(self) -> str:
        """画面と下書きに出す一行。"""
        return (
            f"埋め込み: {self.provenance.described} / 条件: {self._how_of(self.how)}"
            + f" / 判定: {self.judge}"
        )

    def _how_of(self, how: HowToRecall) -> str:
        return f"直近{how.recent_turns}往復・候補{how.found_limit}件・下限{how.relevance_floor}"


@final
@dataclass(frozen=True)
class Covered:
    """前回の範囲。下書きが持つ印で、追記はここから読む。

    どの時刻までの記録を、どのディレクトリから読み、どのファイルを読めずに飛ばし、
    何セッション読んだか、名前の番号をどこまで使ったか、何で候補を引いたか。
    """

    until: datetime
    places: tuple[str, ...]
    skipped: tuple[str, ...]
    sessions: int
    last_exchange: int
    last_case: int
    drawn_with: DrawnWith

    def places_differ_from(self, places: Sequence[str]) -> str | None:
        """記録のディレクトリの集まりが違うか。順序は問わない。"""
        if set(self.places) == set(places):
            return None
        return f"記録のディレクトリ（前回 {sorted(self.places)}、今回 {sorted(places)}）"


@final
@dataclass(frozen=True)
class Added:
    """足す分。追記で下書きの末尾に足すやりとりと問の並び。評価セットではない。

    新しい問の期待は前回のやりとりを指すので、足す分だけでは指す先が揃わない。
    合わせた評価セットの確かめは手順が書く前に済ませる。
    """

    exchanges: tuple[Exchange, ...]
    cases: tuple[Case, ...]


@final
@dataclass(frozen=True)
class Draft:
    """下書き。記録から作った評価セットと、何を取り出したか。

    下書きの問はすべて確認前なので、確認が要る数は問の数と同じで別に持たない。
    飛ばしたファイルはどれかをパスで持ち、画面は数を出す。
    """

    recall_eval: RecallEval
    sessions: int
    spoken: int
    skipped: tuple[str, ...]

    @property
    def exchanges(self) -> int:
        return len(self.recall_eval.exchanges)

    @property
    def cases(self) -> int:
        return len(self.recall_eval.cases)


@final
@dataclass(frozen=True)
class Appended:
    """追記の結果。前回の範囲（追記する前のもの）と、前回・今回・足した分の数。

    渡した数と渡さなかった数の和は新しい発話の数に一致する。埋め込みを動かす道具の
    版が変わったときの注意も運ぶ。
    """

    covered: Covered
    previous_exchanges: int
    previous_cases: int
    new_sessions: int
    incoming: int
    skipped: tuple[str, ...]
    asked: int
    unasked: int
    added_exchanges: int
    added_cases: int
    notice: str | None
