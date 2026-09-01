"""記憶として持つもの。

言葉は docs/150_system/用語集.md を正典とする。
"""

from __future__ import annotations

import math
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

Vector = tuple[float, ...]


@dataclass(frozen=True)
class Prefixes:
    """添え書きの組。埋め込みの AIモデルが文の前に付けるよう定める、側ごとの決まった語。

    覚える側と問い合わせ側で別の語を定めるものがある。語を変えると数の並びの
    作り方が変わるため、出自に含めてインデックスの名前に効かせる。
    """

    remember: str
    recall: str

    @property
    def code(self) -> str:
        """名前へ挟む 8 桁の十六進の符号。

        語そのものは名前に入れない。名前は保存先で完全一致で照合される ASCII の
        識別子だからである。区切りを挟むのは、語の切れ目を移しただけの組が同じ
        符号にならないため。組み込みの `hash` は起動ごとに変わるので使わない。
        """
        return f"{zlib.crc32(f'{self.remember}\n{self.recall}'.encode()):08x}"


@dataclass(frozen=True)
class Provenance:
    """埋め込みの出自。何で作られたか。

    AIモデルの名前（AIを使わなければ無し）と、動かした道具の名前と版と、添え書きの組
    （定めるものだけ）。インデックスの名前はここから組む。規則を各実装が持つと片方だけ
    変わって名前が黙ってずれるため、ここが一箇所で持つ。
    """

    ai_model: str | None
    tool: str
    tool_version: str
    prefixes: Prefixes | None = None

    @property
    def index_name(self) -> str:
        """添え書きが無ければ `AIモデルの名前/道具-版`。有れば名前の直後に `+符号` を挟む。"""
        tooling = f"{self.tool}-{self.tool_version}"
        if self.ai_model is None:
            return tooling
        marked = self.ai_model if self.prefixes is None else f"{self.ai_model}+{self.prefixes.code}"
        return f"{marked}/{tooling}"

    @property
    def described(self) -> str:
        """人が読む形。AIモデルの名前と、括弧で道具の名前と版。添え書きは含めない。

        下書きの「候補を引いた」行にも出る文であり、添え書きを含めるとそちらの画面が
        変わる。添え書きの語は測る入口が別に組む。
        """
        return f"{self.ai_model or 'AIモデル無し'}（{self.tool}-{self.tool_version}）"

    @property
    def prefixes_described(self) -> str:
        """添え書きの語を人が読む形。無ければ「無し」。"""
        if self.prefixes is None:
            return "無し"
        return f"覚える「{self.prefixes.remember}」 問い合わせ「{self.prefixes.recall}」"


@dataclass(frozen=True)
class Dweller:
    """宿り。名前を持つ一人ぶんの存在で、記憶と状態はすべてここに属する。"""

    id: str
    owner: str
    name: str
    nickname: str


@dataclass(frozen=True)
class Identity:
    """名乗り。持ち主が書いた一続きの文章で、書き直すたびに版が増える。"""

    version: int
    text: str


@dataclass(frozen=True)
class Episode:
    """出来事の記憶。一度のやりとりを原文のまま持ち、消さない。"""

    id: int
    utterance: str
    reply: str
    identity_version: int
    happened_at: datetime


@dataclass(frozen=True)
class Retrieval:
    """思い出した記録。思い出しやすさはここから求める。

    求め方は未決のため、この増分では記録そのものを見せる。
    """

    count: int
    last_at: datetime | None


@dataclass(frozen=True)
class Found:
    """探し当てた記憶。

    近さと思い出した記録は別の値として持つ。一つの点数へ混ぜない。どの道で
    出たかも持つ。道が複数あるとき、何が効いたかを読めなくなるためである。
    """

    episode: Episode
    relevance: float
    retrieval: Retrieval
    way: str


@dataclass(frozen=True)
class HowToRecall:
    """思い出し方の値。

    評価セットで測って決めた。既定の埋め込み（`ruri-v3-30m`、INC-031）では、実際の
    会話から作った評価セット（29 問）で下限 0.85 のとき 22 問を満たし無関係な記憶が
    混ざらない。0.84 で混ざりはじめ（2 問）、0.86 で必要なものが出なくなる（16 問）。
    帯は一点しか無く薄い。この埋め込みは近さの値の幅が狭く（無関係でも 0.76 ほど、
    言い換えで 0.85 前後）、0.01 の違いが効く。架空の評価セット（5 問）は 0.85 で
    全問を満たす。埋め込みを替えたら測り直す。値の意味は埋め込みごとに違う（前の
    多言語の AIモデルでは 0.48 から 0.52 の帯だった）。

    直近の往復数は 2 から 6 で差が出ず、8 で期待する記憶が直近へ入って測れなく
    なった。一度に渡す件数は 3、5、8 で差が出なかった。

    評価セットは架空も実際の会話も小さい。問を足したら測り直す。
    """

    recent_turns: int = 6
    found_limit: int = 5
    relevance_floor: float = 0.85


# 気持ちが薄れる半減期。測っていない仮置きで、実際に使って直す（未決事項「気持ちが薄れる速さ」）。
# 一晩置けば会話の余韻がほぼ消え、休憩を挟んだ程度なら残る、という見当で置いた。
MOOD_HALF_LIFE = timedelta(hours=6)
# 性格が薄れる半減期と、一往復の動きが性格に効く割合。どちらも仮置き（未決事項「性格が変わって
# よい範囲」）。一日の会話で振り切れず、季節が変わるほど続けば傾向として残る、という見当。
CHARACTER_HALF_LIFE = timedelta(days=90)
CHARACTER_WEIGHT = 0.1


@dataclass(frozen=True)
class Fading:
    """薄れ方。動きのそれぞれを経過時間で半減期の指数で薄め、係数を掛けて足す。

    気持ちと性格は、この係数と半減期が違うだけの同じ計算である（ADR-005）。AIモデルを
    呼ばず、同じ入力から同じ値になる。−1 と +1 の間に収める。
    """

    half_life: timedelta
    weight: float = 1.0

    def sum(self, shifts: Iterable[Shift], now: datetime) -> float:
        total = 0.0
        for shift in shifts:
            elapsed = max((now - shift.at).total_seconds(), 0.0)
            total += (
                shift.delta * self.weight * math.pow(0.5, elapsed / self.half_life.total_seconds())
            )
        return max(-1.0, min(1.0, total))


@dataclass(frozen=True)
class Moved:
    """一度のやりとりで気持ちがどちらへどれだけ動いたかと、なぜか。応対を作る側が答える。"""

    delta: float
    cause: str

    def __post_init__(self) -> None:
        # 軸の定義は −1〜+1。範囲外を持てると、声の実装ごとに収め忘れた値が記録に残る。
        if not -1.0 <= self.delta <= 1.0:
            raise ValueError(f"動きは −1 から +1 の間: {self.delta}")

    @classmethod
    def unmoved(cls) -> Moved:
        return cls(delta=0.0, cause="動きなし")


@dataclass(frozen=True)
class Shift:
    """動き。いつ、どの往復で、どちらへどれだけ、なぜ動いたかの記録。上書きしない（ADR-007）。"""

    at: datetime
    delta: float
    cause: str
    episode_id: int | None


@dataclass(frozen=True)
class Mood:
    """気持ちの現在値。−1 が沈む、+1 が明るい。動きから求め、保存しない。"""

    value: float

    @classmethod
    def from_shifts(cls, shifts: Iterable[Shift], now: datetime) -> Mood:
        return cls(value=Fading(MOOD_HALF_LIFE).sum(shifts, now))

    @property
    def described(self) -> str:
        return Axis(self.value).described


@dataclass(frozen=True)
class Character:
    """性格の現在値。応対の傾向。気持ちと同じ動きから、長い半減期と小さな係数で求める。"""

    value: float

    @classmethod
    def from_shifts(cls, shifts: Iterable[Shift], now: datetime) -> Character:
        return cls(value=Fading(CHARACTER_HALF_LIFE, CHARACTER_WEIGHT).sum(shifts, now))

    @property
    def described(self) -> str:
        return Axis(self.value).described


@dataclass(frozen=True)
class State:
    """今の状態。気持ちと性格。思い出したことに添え、前置きに渡す。"""

    mood: Mood
    character: Character

    @classmethod
    def from_shifts(cls, shifts: Iterable[Shift], now: datetime) -> State:
        kept = tuple(shifts)
        return cls(mood=Mood.from_shifts(kept, now), character=Character.from_shifts(kept, now))


@dataclass(frozen=True)
class Axis:
    """一本の軸。−1 沈む ↔ +1 明るい。気持ちと性格が同じ軸を使い、同じ言葉で読む。"""

    value: float

    @property
    def described(self) -> str:
        """値を人が読む言葉に。帯で三つに分ける。"""
        if self.value <= -0.3:
            return "沈んでいる"
        if self.value >= 0.3:
            return "明るい"
        return "落ち着いている"


@dataclass(frozen=True)
class Recollection:
    """思い出したこと。

    直近のやりとりと探した記憶は別の道で来る。混ぜて一つの並びにしない。今の状態（気持ちと性格）も添える。
    """

    identity: Identity
    recent: tuple[Episode, ...]
    found: tuple[Found, ...]
    state: State
