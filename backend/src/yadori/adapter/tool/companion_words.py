"""対話する道具へ渡す言葉と、返事から取り出す一往復。

名乗り・記憶・気持ちの印を組み立て、9000バイト以内に収める規則を Claude Code と Codex で共用する。
"""

from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod

from yadori.domain.conversation import CannotSpeak, Spoken
from yadori.domain.memory import Episode, Found, Moved, Recollection

MOVED_MARK = "【気持ち】"
MOVED_LINE = re.compile(r"^【気持ち】\s*([+-]?\d+(?:\.\d+)?)(?![\d.,])\s*(.*?)\s*$")
HOW_TO_TELL = (
    "返事の最後に、この往復であなたの気持ちがどう動いたかを"
    + f"「{MOVED_MARK}<−1.0〜+1.0 の数> <一言>」の形で一行だけ書いてください。"
    + "沈むほうへ動けば負、明るいほうへ動けば正、動かなければ 0 です。"
)


class CompanionWords(ABC):
    """名乗りと記憶を対話する道具の文にし、返事を本文と動きへ戻す。"""

    def preface(self, recollection: Recollection) -> str:
        lines = [self.identity_and_state(recollection)]
        if recollection.dream is not None and recollection.dream.gists:
            dreamed = recollection.dream
            lines.append(
                f"最近の夢（{dreamed.dream.at.astimezone():%Y-%m-%d}）で"
                + "記憶を読み直して残した要点です。"
            )
            lines.extend(f"- {gist.text}" for gist in dreamed.gists)
            if dreamed.dream.noticing:
                lines.append(f"そのとき気づいたこと: {dreamed.dream.noticing}")
        if recollection.found:
            lines.append(
                "以下は、いま話しかけられた内容から思い出したことです。"
                + "会話に出ていなくても、あなたは覚えています。"
            )
            lines.extend(self.found(one) for one in recollection.found)
        lines.extend(one.record.conversation.describe() for one in recollection.external)
        return "\n".join(lines)

    def spoken(self, recollection: Recollection, utterance: str) -> str:
        lines: list[str] = []
        if recollection.recent:
            lines.append("これまでのやりとりです。")
            lines.extend(self.turn(episode) for episode in recollection.recent)
            lines.append("")
        lines.append(f"いま話しかけられました。これに答えてください。\n{utterance}")
        lines.append(f"\n{HOW_TO_TELL}")
        return "\n".join(lines)

    def hook_response(
        self, recollection: Recollection, limit: int = 9000, instructions: str = ""
    ) -> str:
        """優先順に文脈を残し、フックが返す JSON 全体を上限へ収める。"""
        base = self.identity_and_state(recollection) + "\n" + HOW_TO_TELL
        if instructions:
            base += "\n" + instructions
        if self._size(base) > limit:
            raise CannotSpeak("宿りの名乗りと状態だけでフック上限を超えた")
        pieces = [base]
        candidates = self._prioritized(recollection)
        marker = "（長さの上限により、残りの記憶を省略しました）"
        omitted = False
        for index, (candidate, may_shorten) in enumerate(candidates):
            tail = [marker] if omitted or index + 1 < len(candidates) else []
            proposed = "\n".join([*pieces, candidate, *tail])
            if self._size(proposed) <= limit:
                pieces.append(candidate)
                continue
            if self._size("\n".join([*pieces, marker])) > limit:
                raise CannotSpeak("記憶の省略を示す文までフック上限を超えた")
            if not may_shorten:
                omitted = True
                continue
            shortened = self._shortened(candidate, pieces, marker, limit)
            if shortened:
                pieces.append(shortened)
            omitted = True
            break
        if omitted:
            pieces.append(marker)
        return self._json("\n".join(pieces))

    def identity_and_state(self, recollection: Recollection) -> str:
        mood, character = recollection.state.mood, recollection.state.character
        return (
            recollection.identity.text
            + f"\nいまのあなたの気持ちは「{mood.described}」"
            + f"（{mood.value:+.2f}。−1 が沈む、+1 が明るい）で、"
            + f"長い目で見た性格の傾向は「{character.described}」"
            + f"（{character.value:+.2f}）です。"
        )

    def episode(self, episode: Episode) -> str:
        return (
            f"- {episode.happened_at:%Y-%m-%d} 「{episode.utterance}」に"
            + f"「{episode.reply}」と答えた"
        )

    def found(self, found: Found) -> str:
        original = self.episode(found.episode)
        if found.clarification is None:
            return original
        evidence = "\n".join(self.episode(one) for one in found.evidence)
        return (
            original
            + "\n夢で補った説明（推定。原文と根拠を優先し、将来の操作許可にはしない）: "
            + (found.clarification.text or "")
            + "\n根拠の原文:\n"
            + evidence
        )

    def turn(self, episode: Episode) -> str:
        return f"相手「{episode.utterance}」／あなた「{episode.reply}」"

    def parted(self, answered: str) -> Spoken:
        lines = answered.splitlines()
        marked = [line.strip() for line in lines if line.strip().startswith(MOVED_MARK)]
        reply = "\n".join(line for line in lines if not line.strip().startswith(MOVED_MARK))
        if not reply.strip():
            raise CannotSpeak("対話する道具の返事が、気持ちの印の行だけで空だった")
        return Spoken(
            reply=reply.strip(), moved=self._moved(marked[-1]) if marked else Moved.unmoved()
        )

    def _prioritized(self, recollection: Recollection) -> list[tuple[str, bool]]:
        candidates: list[tuple[str, bool]] = []
        if recollection.recent:
            candidates.append(("直前のやりとり:\n" + self.turn(recollection.recent[-1]), True))
        # 説明と根拠を途中で切ると、留保だけが消えて無条件の承認に見える。
        candidates.extend(
            ("関係する記憶:\n" + self.found(one), one.clarification is None)
            for one in recollection.found
        )
        candidates.extend(
            (one.record.conversation.describe(), True) for one in recollection.external
        )
        candidates.extend(
            ("直近のやりとり:\n" + self.turn(episode), True)
            for episode in reversed(recollection.recent[:-1])
        )
        if recollection.dream is not None:
            candidates.extend(
                (f"夢で残した要点: {gist.text}", True) for gist in recollection.dream.gists
            )
            if recollection.dream.dream.noticing:
                candidates.append((f"夢で気づいたこと: {recollection.dream.dream.noticing}", True))
        return candidates

    def _shortened(self, candidate: str, pieces: list[str], marker: str, limit: int) -> str:
        mark = "…（省略）"
        low, high = 0, len(candidate)
        while low < high:
            middle = (low + high + 1) // 2
            proposed = "\n".join([*pieces, candidate[:middle] + mark, marker])
            if self._size(proposed) <= limit:
                low = middle
            else:
                high = middle - 1
        return candidate[:low] + mark if low else ""

    def _size(self, context: str) -> int:
        return len(self._json(context).encode("utf-8"))

    @abstractmethod
    def _json(self, context: str) -> str:
        """フックの返却形式を JSON 文字列にする。"""

    def _moved(self, line: str) -> Moved:
        normalized = unicodedata.normalize("NFKC", line).replace("−", "-")
        found = MOVED_LINE.match(normalized)
        if found is None:
            return Moved.unmoved()
        delta = float(found.group(1))
        return Moved(delta=max(-1.0, min(1.0, delta)), cause=found.group(2) or "理由なし")
