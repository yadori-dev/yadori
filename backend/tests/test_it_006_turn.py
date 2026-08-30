"""INC-006 の結合テスト。一往復の手順を確かめる。

架空の宿りで書き、利用者の実際の会話は使わない。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.sora import ABOUT_TOMATO, FILLERS, HOW, NAME_DECLARED, PLANTED, SORA, Ticking, talk
from yadori.adapter.embedding.characters import CharacterPairs
from yadori.adapter.store import SqliteMemories
from yadori.domain.conversation import CannotSpeak
from yadori.domain.memory import Recollection
from yadori.usecase.conversation import Conversation, Turn


class _Counting:
    """思い出したことを数え上げるだけの声。模型を呼ばない。

    応対そのものの質はここでは見ない。見るのは、名乗りと思い出したことが
    渡っていることと、一往復の順序である。
    """

    def speak(self, recollection: Recollection, utterance: str) -> str:
        del utterance
        return (
            f"名乗り{recollection.identity.version}版"
            f"／直近{len(recollection.recent)}件"
            f"／探した{len(recollection.found)}件"
        )


class _Silent:
    """応対を作れない声。"""

    def speak(self, recollection: Recollection, utterance: str) -> str:
        del recollection, utterance
        raise CannotSpeak("模型が応えない")


@pytest.fixture
def memories(tmp_path: Path) -> Iterator[SqliteMemories]:
    kept = SqliteMemories(tmp_path / "test.sqlite")
    kept.settle(SORA)
    _ = kept.write_identity(SORA.id, NAME_DECLARED)
    yield kept
    kept.close()


def conversation_of(memories: SqliteMemories) -> Conversation:
    return Conversation(memories, CharacterPairs(), Ticking(), HOW)


def test_IT_006_004_一往復で思い出し応対し覚える(memories: SqliteMemories) -> None:
    conversation = conversation_of(memories)
    talk(conversation, [PLANTED, *FILLERS])
    turn = Turn(conversation, _Counting())

    response = turn.respond_to(SORA.id, ABOUT_TOMATO)

    # 名乗りと、二つの道で来た記憶が、応対を作る相手へ渡っている。
    assert response.reply == "名乗り1版／直近6件／探した1件"
    assert response.recollection.identity.text == NAME_DECLARED
    # 交わした一往復が、応対そのものと一緒に記憶へ残る。
    assert memories.count_episodes(SORA.id) == len(FILLERS) + 2
    assert response.episode.utterance == ABOUT_TOMATO
    assert response.episode.reply == response.reply


def test_IT_006_004_応対を作れないと覚えないが思い出した記録は残る(
    memories: SqliteMemories,
) -> None:
    talk(conversation_of(memories), [PLANTED, *FILLERS])
    kept = memories.count_episodes(SORA.id)
    planted_id = 1

    silent = Turn(conversation_of(memories), _Silent())
    with pytest.raises(CannotSpeak):
        _ = silent.respond_to(SORA.id, ABOUT_TOMATO)

    assert memories.count_episodes(SORA.id) == kept
    # 思い出したこと自体は起きているため、記録は残る。
    assert memories.retrieval(planted_id).count == 1


def test_IT_006_004_直近で渡した記憶は思い出した記録に数えない(
    memories: SqliteMemories,
) -> None:
    conversation = conversation_of(memories)
    talk(conversation, [PLANTED, *FILLERS])
    turn = Turn(conversation, _Counting())

    response = turn.respond_to(SORA.id, ABOUT_TOMATO)

    # 直近は常に渡るため、数えると新しさが思い出しやすさへ流れ込む。
    for episode in response.recollection.recent:
        assert memories.retrieval(episode.id).count == 0
    for one in response.recollection.found:
        assert memories.retrieval(one.episode.id).count == 1
