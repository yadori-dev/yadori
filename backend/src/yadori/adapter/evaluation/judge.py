"""判定の実装。対話する道具に、後の発話が前のどの発話の話題を指すかを答えさせる。

道具へ送るのは発話の文章と並びの番号だけで、返事、時刻、作業場所は送らない
（ADR-017）。道具の文脈は外して呼ぶ。作業場所ごとに置かれた指示が判定に
混ざるためである。返事は番号の組として受け取り、形、範囲、向きをここで確かめ、
続けられなければ下書きの失敗として返す。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Protocol, final

from yadori.domain.evaluation import CannotDraft, Pair

PREFACE = (
    "あなたは会話の記録を読み、番号付きの発話の並びの中で、後の発話が前のどの発話の"
    "話題を指しているかを判定する係です。同じ話題の続き、言い直し、蒸し返しを組にし、"
    "話題が違うものは組にしません。同意、承諾、指示、相槌だけで話題を持たない発話は、"
    "組にしません。前の発話を明確に指していない発話も含めません。"
    "返事は JSON の配列だけにし、説明を書きません。"
)
ASKING = (
    "次の番号付きの発話の並びを、先頭から順に一つずつ確かめてください。各発話について、"
    "それより前の発話の中に同じ話題（同じ物事、同じ作業、同じ相談、同じ人や場所）を"
    "扱っているものがあれば、その全部の番号を earlier に挙げます。語が同じでなくても、"
    "言い直しや蒸し返しなら同じ話題です。同意、承諾、指示、相槌だけで話題を持たない"
    "発話は挙げません。見つけた組は数が多くても全部返してください。\n"
    '返事は [{"later": 後の番号, "earlier": [前の番号, ...]}, ...] の JSON 配列だけにし、'
    "earlier は必ず later より小さい番号にします。一つも無ければ [] を返します。\n\n"
)
NARROW_HINT = "。置き場を絞って指し直すと通ることがあります"
_FENCE = re.compile(r"^```[a-z]*\s*|\s*```$", re.MULTILINE)


class _Call(Protocol):
    def ask(self, preface: str, spoken: str) -> str: ...


@final
class ClaudeCodeJudge:
    def __init__(self, call: _Call) -> None:
        self._call: _Call = call

    def pairs(self, utterances: Sequence[str]) -> tuple[Pair, ...]:
        """一つの作業場所の発話の並びを渡し、組を受け取る。"""
        if not utterances:
            return ()
        answered = self._asked(utterances)
        return self._as_pairs(answered, len(utterances))

    def _asked(self, utterances: Sequence[str]) -> str:
        numbered = "\n".join(f"{number}: {text}" for number, text in enumerate(utterances, start=1))
        try:
            return self._call.ask(PREFACE, ASKING + numbered)
        except Exception as trouble:
            raise CannotDraft(f"判定を続けられません: {trouble}{NARROW_HINT}") from trouble

    def _as_pairs(self, answered: str, count: int) -> tuple[Pair, ...]:
        try:
            parsed: object = json.loads(_FENCE.sub("", answered.strip()))  # pyright: ignore[reportAny]
        except json.JSONDecodeError as broken:
            raise CannotDraft("判定の返事が JSON として読めません") from broken
        if not isinstance(parsed, list):
            raise CannotDraft("判定の返事の形が違います（配列でない）")
        pairs: list[Pair] = []
        for item in parsed:  # pyright: ignore[reportUnknownVariableType]
            pairs.extend(self._pairs_of(item, count))  # pyright: ignore[reportUnknownArgumentType]
        return tuple(pairs)

    def _pairs_of(self, item: object, count: int) -> list[Pair]:
        if not isinstance(item, dict):
            raise CannotDraft("判定の返事の形が違います（組が辞書でない）")
        later: object = item.get("later")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        earliers: object = item.get("earlier")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if not isinstance(later, int) or not isinstance(earliers, list):
            raise CannotDraft("判定の返事の形が違います（番号でない）")
        found: list[Pair] = []
        for earlier in earliers:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(earlier, int):
                raise CannotDraft("判定の返事の形が違います（番号でない）")
            if not (1 <= earlier < later <= count):
                raise CannotDraft(f"判定が範囲外か向き違いの番号を指しました: {earlier} → {later}")
            found.append(Pair(later=later - 1, earlier=earlier - 1))
        return found
