"""手元の対話する道具を通して、選んだ往復から要点と気づきを書く。

応対と同じ相手、同じ呼び方（ADR-016）。渡すのは名乗りと、選んだ往復の発話と返事だけ。
返りは決まった形（要点は「- 」で始まる行、最後に「気づき: …」）で書かせ、切り出す。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, final

from yadori.adapter.tool import ToolCallFailed
from yadori.domain.dream import CannotDream, Summarized
from yadori.domain.memory import Episode, Identity

HOW_TO_SUMMARIZE = (
    "以下は、あなたが最近交わしたやりとりのうち、残しておくと決めたものです。話題ごとに"
    + "要点を一行ずつ、「- 」で始めて書いてください。細部は落とし、後で思い出すのに要る"
    + "ことだけを残します。最後に、離れた話題どうしが結びついて気づいたことがあれば"
    + "「気づき: 」に続けて一文で、無ければ「気づき: 無し」と書いてください。前置きは要りません。"
)
NOTICING_MARK = "気づき:"
BULLETS = ("-", "・", "*", "－")
NONE_WORDS = ("無し", "なし", "ありません", "特になし", "特に無し")


class _Call(Protocol):
    def ask(self, preface: str, spoken: str) -> str: ...


@final
class ClaudeCodeSummarizing:
    def __init__(self, call: _Call) -> None:
        self._call: _Call = call

    def summarize(self, identity: Identity, episodes: Sequence[Episode]) -> Summarized:
        """名乗りを前置きに、選んだ往復を渡し、要点の並びと気づきを切り出す。"""
        lines = [HOW_TO_SUMMARIZE, ""]
        lines.extend(
            f"{one.happened_at:%Y-%m-%d %H:%M} 相手「{one.utterance}」／あなた「{one.reply}」"
            for one in episodes
        )
        try:
            answered = self._call.ask(identity.text, "\n".join(lines))
        except ToolCallFailed as trouble:
            raise CannotDream(str(trouble)) from trouble
        return self._parted(answered)

    def _nothing(self, said: str) -> bool:
        """「無し」の言い方の揺れ（句点、余白、言い換え）を無しとみなす。"""
        bare = said.strip().rstrip("。．.").strip()
        return not bare or bare in NONE_WORDS

    def _parted(self, answered: str) -> Summarized:
        gists: list[str] = []
        noticing: str | None = None
        for raw in answered.splitlines():
            line = raw.strip()
            if line.startswith(BULLETS):
                # 先頭の記号を一つ落とす。記号だけの行は要点にしない。
                said = line[1:].strip()
                if said:
                    gists.append(said)
            elif line.startswith(NOTICING_MARK):
                said = line[len(NOTICING_MARK) :].strip()
                noticing = None if self._nothing(said) else said
        if not gists:
            raise CannotDream("対話する道具が要点を一つも書かなかった")
        return Summarized(gists=tuple(gists), noticing=noticing)
