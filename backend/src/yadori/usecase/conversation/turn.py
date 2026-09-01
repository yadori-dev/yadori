"""一往復。

宿り自身が話す場所で、話しかけられてから覚えるまでを一つの手順として持つ。
上から読めば、一度の会話で何が起きるかが並ぶ。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from yadori.domain.conversation import Spoken, Voice
from yadori.domain.memory import Episode, Mood, Moved, Recollection
from yadori.usecase.conversation.service import Conversation


@final
@dataclass(frozen=True)
class Response:
    """一往復の結果。

    応対だけでなく、何を思い出して作ったかも返す。見当違いの応対の理由を
    後から追えるようにするためである。
    """

    reply: str
    recollection: Recollection
    episode: Episode
    moved: Moved
    mood: Mood


@final
class Turn:
    """話しかけられてから覚えるまでの一往復。"""

    def __init__(self, conversation: Conversation, voice: Voice) -> None:
        self._conversation: Conversation = conversation
        self._voice: Voice = voice

    def respond_to(self, dweller_id: str, utterance: str) -> Response:
        """話しかけられて、応対して、覚える。

        - 思い出す
        - 応対を作る（気持ちがどう動いたかも返る）
        - 覚える（動きも積む）

        応対を作れなければ覚えない。作れなかった往復を覚えると、次に思い出す
        材料が実際には交わしていない会話で埋まる。
        """
        recollection = self._recall(dweller_id, utterance)
        spoken = self._speak(recollection, utterance)
        episode = self._remember(dweller_id, utterance, spoken)
        return Response(
            reply=spoken.reply,
            recollection=recollection,
            episode=episode,
            moved=spoken.moved,
            mood=self._conversation.mood(dweller_id),
        )

    def rebuild_index(self, dweller_id: str) -> int:
        """いまの埋め込みのインデックスが無い記憶へ、インデックスを作る。"""
        return self._conversation.rebuild_index(dweller_id)

    def _recall(self, dweller_id: str, utterance: str) -> Recollection:
        """関係する記憶と名乗りを取り出す。

        ここで思い出した記録が積まれる。応対を作れなくても、思い出したこと
        自体は起きているため取り消さない。
        """
        return self._conversation.recall(dweller_id, utterance)

    def _speak(self, recollection: Recollection, utterance: str) -> Spoken:
        """思い出したことと名乗りから、応対の文章を作る。"""
        return self._voice.speak(recollection, utterance)

    def _remember(self, dweller_id: str, utterance: str, spoken: Spoken) -> Episode:
        """交わした一往復を原文のまま記憶へ加え、その往復の動きを積む。"""
        return self._conversation.remember(dweller_id, utterance, spoken.reply, spoken.moved)
