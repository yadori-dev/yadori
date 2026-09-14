"""固定した架空例の検索品質。最新の夢・直近・外部の履歴を使わず前後を測る。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import final

import pytest

from tests.test_st_080_clarification import AT, DWELLER, FIXTURES, ObservedCall
from yadori.adapter.dream.clarification import ToolExplaining
from yadori.adapter.embedding import DefaultEmbeddings
from yadori.adapter.store import InMemoryMemories
from yadori.domain.memory import Clarification, Episode, HowToRecall
from yadori.usecase.conversation import Conversation
from yadori.usecase.dream.clarification import Clarifying


@final
class Cases:
    def __init__(self, holdout: bool = True) -> None:
        self.observed = "holdout-observed.json" if holdout else "observed.json"
        filename = "holdout-cases.json" if holdout else "cases.json"
        self.values: list[dict[str, object]] = json.loads((FIXTURES / filename).read_text())[
            "cases"
        ]

    def explain(self, target: Episode, preceding: tuple[Episode, ...], at: object) -> Clarification:
        del at
        case = next(
            one
            for one in self.values
            if one["proposal"] == preceding[-1].reply and one["utterance"] == target.utterance
        )
        return ToolExplaining(ObservedCall(str(case["name"]), self.observed)).explain(
            target, preceding, AT
        )


@final
class Quality:
    def run(
        self,
        cache: Path | None = None,
        *,
        holdout: bool = True,
        clarification_floor: float = 0.90,
    ) -> dict[str, object]:
        embeddings = DefaultEmbeddings()(cache)
        memories = InMemoryMemories()
        memories.settle(DWELLER)
        _ = memories.write_identity(DWELLER.id, "わたしはそらです")
        conversation = Conversation(
            memories, embeddings, lambda: AT, HowToRecall(0, 5, 0.85, clarification_floor)
        )
        cases = Cases(holdout)
        targets: dict[str, int] = {}
        originals: dict[str, int] = {}
        for case in cases.values:
            name = str(case["name"])
            previous = conversation.remember(
                DWELLER.id,
                "今から相談したいことがあるので提案を一つ聞かせてください",
                str(case["proposal"]),
                source=name + ":proposal",
                session_id=name,
            )
            target = conversation.remember(
                DWELLER.id,
                str(case["utterance"]),
                "返答を受け取りました",
                source=name + ":answer",
                session_id=name,
                previous_source=previous.source,
            )
            targets[name] = target.id
            originals[name] = previous.id
        before = self._measured(conversation, cases, targets, originals)
        result = Clarifying(memories, embeddings, cases, lambda: AT).run(DWELLER.id)
        _ = memories.record_dream(DWELLER.id, AT, AT, AT, 0, 0, "別の夢")
        after = self._measured(conversation, cases, targets, originals)
        return {
            "before": before,
            "after": after,
            "adopted": sum(one.status == "clarified" for one in result.records),
            "deferred": sum(one.status == "deferred" for one in result.records),
            "unneeded": sum(one.status == "unneeded" for one in result.records),
            "embedding": embeddings.name,
            "raw_floor": 0.85,
            "clarification_floor": clarification_floor,
            "top_k": 5,
            "recent": 0,
        }

    def _measured(
        self,
        conversation: Conversation,
        cases: Cases,
        targets: dict[str, int],
        originals: dict[str, int],
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for case in cases.values:
            query = case["query"]
            if not isinstance(query, str):
                continue
            name = str(case["name"])
            found = conversation.recall(DWELLER.id, query).found
            ids = {one.episode.id for one in found}
            forbidden = {
                identifier
                for other in targets
                if other != name
                for identifier in (targets[other], originals[other])
            }
            rows.append(
                {
                    "name": name,
                    "expected": targets[name] in ids,
                    "forbidden": bool(ids & forbidden),
                    "found": [
                        {
                            "id": one.episode.id,
                            "text": one.episode.utterance,
                            "score": one.relevance,
                        }
                        for one in found
                    ],
                }
            )
        return rows


@pytest.mark.contract
def test_ST_080_007_固定条件で期待する検索が増え無関係な検索が増えない() -> None:
    result = Quality().run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    before = result["before"]
    after = result["after"]
    assert isinstance(before, list) and isinstance(after, list)
    before_hits = sum(one["expected"] is True for one in before)  # pyright: ignore[reportUnknownVariableType]
    after_hits = sum(one["expected"] is True for one in after)  # pyright: ignore[reportUnknownVariableType]
    before_wrong = sum(one["forbidden"] is True for one in before)  # pyright: ignore[reportUnknownVariableType]
    after_wrong = sum(one["forbidden"] is True for one in after)  # pyright: ignore[reportUnknownVariableType]
    assert after_hits > before_hits
    assert after_wrong <= before_wrong
