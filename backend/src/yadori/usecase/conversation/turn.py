"""一往復。

宿り自身が話す場所で、話しかけられてから覚えるまでを一つの手順として持つ。
上から読めば、一度の会話で何が起きるかが並ぶ。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from yadori.domain.conversation import Voice
from yadori.domain.memory import Episode, Recollection
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


@final
class Turn:
    """話しかけられてから覚えるまでの一往復。"""

    def __init__(self, conversation: Conversation, voice: Voice) -> None:
        self._conversation: Conversation = conversation
        self._voice: Voice = voice

    def respond_to(self, dweller_id: str, utterance: str) -> Response:
        """話しかけられて、応対して、覚える。

        - 思い出す
        - 応対を作る
        - 覚える

        応対を作れなければ覚えない。作れなかった往復を覚えると、次に思い出す
        材料が実際には交わしていない会話で埋まる。
        """
        recollection = self._recall(dweller_id, utterance)
        reply = self._speak(recollection, utterance)
        episode = self._remember(dweller_id, utterance, reply)
        return Response(reply=reply, recollection=recollection, episode=episode)

    def _recall(self, dweller_id: str, utterance: str) -> Recollection:
        """関係する記憶と名乗りを取り出す。

        ここで思い出した記録が積まれる。応対を作れなくても、思い出したこと
        自体は起きているため取り消さない。
        """
        return self._conversation.recall(dweller_id, utterance)

    def _speak(self, recollection: Recollection, utterance: str) -> str:
        """思い出したことと名乗りから、応対の文章を作る。"""
        return self._voice.speak(recollection, utterance)

    def _remember(self, dweller_id: str, utterance: str, reply: str) -> Episode:
        """交わした一往復を、原文のまま記憶へ加える。"""
        return self._conversation.remember(dweller_id, utterance, reply)
