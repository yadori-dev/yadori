"""判定の実装。対話する道具に、後の発話の候補のうちどれが同じ話題かを答えさせる。

道具へ送るのは、後の発話と候補の発話の文章（改行は潰す）と番号だけで、返事、時刻、
作業場所は送らない（ADR-017）。作業場所の発話を丸ごと送ることはない。道具の文脈は
外して呼ぶ。返事は番号の組として受け取り、形と範囲をここで確かめ、続けられなければ
下書きの失敗として返す。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Protocol, final

from yadori.adapter.tool import ToolCallFailed
from yadori.domain.evaluation import Asking, CannotDraft, Pair

PREFACE = (
    "あなたは会話の記録を読み、後の発話と、その前に交わされた発話の候補を見て、候補の"
    "うちどれが後の発話と同じ話題（同じ物事、同じ作業、同じ相談、同じ人や場所）かを"
    "判定する係です。言い直しや蒸し返しなら語が同じでなくても同じ話題です。話題が"
    "違うもの、同意や承諾や指示だけで話題を持たない後の発話は組にしません。"
    "返事は JSON の配列だけにし、説明を書きません。"
)
ASKING = (
    "次の問いごとに、候補のうち後の発話と同じ話題のものの番号を挙げてください。"
    '返事は [{"q": 問いの番号, "same": [候補の番号, ...]}, ...] の JSON 配列だけにします。'
    "同じ話題の候補が無い問いは same を [] にするか、省いてください。\n\n"
)
_FENCE = re.compile(r"^```[a-z]*\s*|\s*```$", re.MULTILINE)


class _Call(Protocol):
    def ask(self, preface: str, spoken: str) -> str: ...


@final
class ClaudeCodeJudge:
    def __init__(self, call: _Call) -> None:
        self._call: _Call = call

    def pairs(self, askings: Sequence[Asking]) -> tuple[Pair, ...]:
        """問いをいくつか渡し、問いごとに同じ話題と判定された候補の番号を組で受け取る。"""
        if not askings:
            return ()
        answered = self._asked(askings)
        return self._as_pairs(answered, askings)

    def _asked(self, askings: Sequence[Asking]) -> str:
        try:
            return self._call.ask(PREFACE, ASKING + self._written(askings))
        except ToolCallFailed as trouble:
            raise CannotDraft(f"判定を続けられません: {trouble}") from trouble

    def _written(self, askings: Sequence[Asking]) -> str:
        blocks: list[str] = []
        for number, asking in enumerate(askings, start=1):
            lines = [f"問い {number}: {self._one_line(asking.utterance)}"]
            lines.extend(
                f"  候補 {place}: {self._one_line(candidate)}"
                for place, candidate in enumerate(asking.candidates, start=1)
            )
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def _one_line(self, text: str) -> str:
        return " ".join(text.split())

    def _as_pairs(self, answered: str, askings: Sequence[Asking]) -> tuple[Pair, ...]:
        try:
            parsed: object = json.loads(_FENCE.sub("", answered.strip()))  # pyright: ignore[reportAny]
        except json.JSONDecodeError as broken:
            raise CannotDraft("判定の返事が JSON として読めません") from broken
        if not isinstance(parsed, list):
            raise CannotDraft("判定の返事の形が違います（配列でない）")
        pairs: list[Pair] = []
        for item in parsed:  # pyright: ignore[reportUnknownVariableType]
            pairs.extend(self._pairs_of(item, askings))  # pyright: ignore[reportUnknownArgumentType]
        return tuple(pairs)

    def _pairs_of(self, item: object, askings: Sequence[Asking]) -> list[Pair]:
        if not isinstance(item, dict):
            raise CannotDraft("判定の返事の形が違います（組が辞書でない）")
        question: object = item.get("q")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        same: object = item.get("same", [])  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if not isinstance(question, int) or not isinstance(same, list):
            raise CannotDraft("判定の返事の形が違います（番号でない）")
        if not 1 <= question <= len(askings):
            raise CannotDraft(f"判定が無い問いの番号を指しました: {question}")
        candidates = askings[question - 1].candidates
        found: list[Pair] = []
        for place in same:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(place, int):
                raise CannotDraft("判定の返事の形が違います（番号でない）")
            if not 1 <= place <= len(candidates):
                raise CannotDraft(f"判定が候補に無い番号を指しました: 問い {question} の {place}")
            found.append(Pair(later=question - 1, earlier=place - 1))
        return found
