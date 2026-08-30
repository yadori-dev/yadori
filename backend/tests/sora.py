"""テストで使う架空の宿り。

利用者の実際の会話を使わないため、確かめたい規則が読み取れる文言をここに
まとめる。
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime, timedelta

from yadori.domain.memory import Dweller, HowToRecall
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


class Ticking:
    """呼ばれるたびに一分進む時計。I/O 境界なので置き換える。"""

    def __init__(self) -> None:
        self._at = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self._at += timedelta(minutes=1)
        return self._at


def talk(conversation: Conversation, utterances: Collection[str]) -> None:
    for utterance in utterances:
        conversation.remember(SORA.id, utterance, "はい、わかりました")
