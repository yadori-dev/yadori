"""テストで使う架空の宿り。

利用者の実際の会話を使わないため、確かめたい規則が読み取れる文言をここに
まとめる。
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import final

from yadori.adapter.embedding import Announcing
from yadori.domain.memory import Dweller, Embeddings, HowToRecall
from yadori.usecase.conversation import Conversation

SORA = Dweller(id="sora", owner="架空の持ち主", name="そら", nickname="そら")
NAME_DECLARED = "わたしはそらです。ていねいな言葉で話し、園芸を好みます。"

# 値は測って直すものなので、規則を確かめるテストでは明示して固定する。
# ここを直しても、確かめている規則そのものは変わらない。
HOW = HowToRecall(recent_turns=6, found_limit=5, relevance_floor=0.30)

# 一件目は直近から外れ、意味で探す側にだけ現れる。
PLANTED = "トマトを植えました"
# 二件目から四件目も直近から外れる。園芸とも互いとも語が重ならないようにする。
BEYOND_RECENT = [
    "電車が遅れて困った",
    "本を三冊借りた",
    "洗濯物がよく乾いた",
]
# 残りは直近として必ず渡る六件。
RECENT = [
    "きのうは映画を観た",
    "新しい鍵盤が届いた",
    "夕飯がとてもおいしかった",
    "早めに横になった",
    "同僚が休みを取るらしい",
    "近所で工事が始まる",
]
FILLERS = [*BEYOND_RECENT, *RECENT]
ABOUT_TOMATO = "トマトはその後どうなりましたか"
# 指す語だけの発話。意味で探しても何も出ない。
POINTING = "それはどうなった"
UNRELATED = "明日の天気"


@final
class Ticking:
    """呼ばれるたびに一分進む時計。I/O 境界なので置き換える。"""

    def __init__(self) -> None:
        self._at: datetime = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self._at += timedelta(minutes=1)
        return self._at


def talk(conversation: Conversation, utterances: Collection[str]) -> None:
    for utterance in utterances:
        _ = conversation.remember(SORA.id, utterance, "はい、わかりました")


def fixed(embeddings: Embeddings) -> Callable[[Path | None, Announcing | None], Embeddings]:
    """決まった埋め込みを返す工場。既定の埋め込みを組む工場の差し替えに使う。"""

    def factory(cache_dir: Path | None, announcing: Announcing | None) -> Embeddings:
        del cache_dir, announcing
        return embeddings

    return factory


@final
class Steady:
    """一定の歩幅で進む時計。時間の値を二度の実行で同じにするために使う。"""

    def __init__(self, step: float = 0.001) -> None:
        self._at: float = 0.0
        self._step: float = step

    def __call__(self) -> float:
        self._at += self._step
        return self._at
