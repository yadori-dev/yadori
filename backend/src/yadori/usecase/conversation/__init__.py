"""会話の手順。

記憶に対する口は「思い出す」と「覚える」の二つで、この二つが一組である。
宿り自身が話す場所では、その二つを一往復として繋いだ手順を使う。返事の
文章を作る相手は口として受け取り、この層は作らない。
"""

from yadori.usecase.conversation.service import Conversation
from yadori.usecase.conversation.turn import Response, Turn

__all__ = ["Conversation", "Response", "Turn"]
