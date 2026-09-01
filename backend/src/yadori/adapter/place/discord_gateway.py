"""Discord との実物の繋ぎ。

外の道具（discord.py）を使うのはここだけである。届いた言葉を値へ移して場所へ渡し、
返された文を送る。誰に答えるかも、いつ考えている印を出すかも判断しない
（ADR-019、`DiscordPlace` が決める）。

道具の読み込みは待つときまで遅らせる。話す以外の使い方（測る、夢、状態）では要らない。
"""

from __future__ import annotations

import logging
import sys
from typing import final

from yadori.adapter.place.discord import Answering, CannotConnect, Heard


@final
class DiscordGateway:
    """bot として繋ぎ、話しかけられるのを待つ。"""

    def __init__(self, token: str) -> None:
        self._token: str = token

    def listen(self, answering: Answering, greeting: str) -> None:
        """繋いで待つ。終わるまで戻らない。"""
        import discord

        # 受け取るものを直接の会話だけに絞る。サーバーの部屋の言葉はそもそも届かない
        # （ADR-019）。直接の会話の中身は、開発者向けの画面の MESSAGE CONTENT INTENT を
        # 入れなくても読める。
        intents = discord.Intents.none()
        intents.dm_messages = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready() -> None:  # pyright: ignore[reportUnusedFunction]
            print(greeting)

        @client.event
        async def on_message(message: discord.Message) -> None:  # pyright: ignore[reportUnusedFunction]
            # discord.py はここで起きた例外を自前の記録へ流すだけで、外へ出さない。
            # 送れなかった・覚えられなかったを黙って落とさないよう、手元へ書いて次を待つ。
            try:
                heard = Heard(
                    text=message.content,
                    author_id=message.author.id,
                    direct=isinstance(message.channel, discord.DMChannel),
                    from_myself=message.author == client.user,
                )
                for one in await answering(heard, message.channel.typing):
                    _ = await message.channel.send(one)
            except Exception as trouble:
                print(f"Discord の応対でつまずきました: {trouble}", file=sys.stderr)

        # discord.py の警告（送れない、繋ぎ直せない、待たされている）を消さずに手元へ出す。
        # 既定のままにすると毎回の通信まで出るため、警告から上だけにする。
        try:
            client.run(self._token, log_level=logging.WARNING)
        except (discord.LoginFailure, discord.PrivilegedIntentsRequired) as refused:
            raise CannotConnect(f"Discord へ繋げません: {refused}") from refused
