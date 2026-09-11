"""名前で埋め込みを選ぶ。

端末の入口が受けた名前から埋め込みを選ぶ。語の重なり、今の多言語の AIモデル、
試す埋め込みの一覧、道具の対応表の順に解き、名前が無ければ既定を返す。返すのは
どの場合も重さを量る包みで包んだもので、`+` で並べば道ごとに包んだ列である。
取得先と知らせ先は取得する埋め込みにだけ渡す。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import final

from yadori.adapter.embedding.characters import CharacterPairs
from yadori.adapter.embedding.contenders import Contender, Contenders
from yadori.adapter.embedding.default import DefaultEmbeddings
from yadori.adapter.embedding.multilingual import Announcing, Embedder, Multilingual
from yadori.adapter.embedding.table import Table
from yadori.adapter.embedding.weighing import Weighing
from yadori.domain.memory import Embeddings

CHARACTERS = "characters"
MULTILINGUAL = "multilingual"
Factory = Callable[[Path | None, Announcing | None], Embeddings]


class NotAnEmbeddingName(Exception):
    """名前で選べなかった。理由に指せる名前の一覧と、対応表の AIモデルの指し方を含む。

    入口が受けた文字列が解けないという入口側の話で、記憶の規則は使わないため
    domain には置かない。何も取得せずに返す。
    """


@final
class Choosing:
    def __init__(
        self,
        cache_dir: Path | None,
        announcing: Announcing | None = None,
        default: Factory | None = None,
        runner: Callable[[], Embedder] | None = None,
    ) -> None:
        self._cache_dir: Path | None = cache_dir
        self._announcing: Announcing | None = announcing
        self._contenders: Contenders = Contenders()
        self._default: Factory = default or DefaultEmbeddings()
        # 道具の代わり。テストが AIモデルを読み込まずに名前の解き方を確かめるために差し込む。
        self._runner: Callable[[], Embedder] | None = runner

    def pick(self, written: str | None) -> Weighing | list[Weighing]:
        """名前を解いて、包んで返す。`+` で並べば道ごとに包んだ列。

        - 名前が無ければ既定
        - 名前ごとに、語の重なり、今の多言語、一覧、対応表の順に解く
        """
        if written is None:
            return Weighing(self._default(self._cache_dir, self._announcing))
        ways = [Weighing(self._one(name)) for name in written.split("+")]
        return ways[0] if len(ways) == 1 else ways

    def _one(self, name: str) -> Embeddings:
        if name == CHARACTERS:
            # 語の重なりは道具に触らずに解く。道具の無い手元でも動くという約束を守る。
            return CharacterPairs()
        if name == MULTILINGUAL:
            return DefaultEmbeddings().multilingual(self._cache_dir, self._announcing, self._runner)
        listed = self._contenders.named(name)
        if listed is not None:
            return self._registered(listed)
        # 対応表を見るのは `配布元/名前` の形のときだけ。それ以外は道具に触らずに断る。
        described = Table().described(name) if "/" in name else None
        if described is not None:
            return Multilingual(
                model=name,
                ai_model=name.split("/")[-1],
                cache_dir=self._cache_dir,
                source=described.source,
                declared_gb=described.declared_gb,
                announcing=self._announcing,
                runner=self._runner,
            )
        raise NotAnEmbeddingName(
            f"{name} は指せる埋め込みではありません。指せるもの: "
            + "、".join((CHARACTERS, MULTILINGUAL, *self._contenders.names))
            + "。埋め込みを動かす道具の対応表にある AIモデルは 配布元/名前 で指せます"
        )

    def _registered(self, listed: Contender) -> Multilingual:
        """一覧の一件を道具へ登録してから組む。組み方は既定の工場と同じ。"""
        return DefaultEmbeddings().contender(
            listed.name, self._cache_dir, self._announcing, self._runner
        )
