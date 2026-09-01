"""既定の埋め込みを組む工場。

起動、下書きの入口、名前で選ぶ部品の三つが同じ既定を組めるよう、ここが一箇所で持つ。
既定を替えるときに触るのは、この工場、思い出し方の下限の値と説明（`HowToRecall`）、
実物に当てる契約テスト、現在の構造の文書である。

既定は INC-031 で測って `ruri-v3-30m`（日本語向け。有志の ONNX 変換版）にした。実際の
会話の評価セットで、今までの多言語の AIモデルより 7 問多く満たし、混入が無い。
`multilingual` の名前は今までどおり多言語の AIモデルを指す。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import final

from yadori.adapter.embedding.contenders import Contenders
from yadori.adapter.embedding.multilingual import (
    AI_MODEL,
    MODEL,
    Announcing,
    Embedder,
    Multilingual,
)
from yadori.adapter.embedding.table import Table

DEFAULT = "ruri-v3-30m"


@final
class DefaultEmbeddings:
    """名前を指さないときに宿りが使う埋め込みを組む。"""

    def __call__(
        self, cache_dir: Path | None, announcing: Announcing | None = None
    ) -> Multilingual:
        return self.contender(DEFAULT, cache_dir, announcing)

    def contender(
        self,
        name: str,
        cache_dir: Path | None,
        announcing: Announcing | None = None,
        runner: Callable[[], Embedder] | None = None,
    ) -> Multilingual:
        """試す埋め込みの一覧の一件を、道具へ登録してから組む。出自の名前は配布元を含む。"""
        contenders = Contenders()
        listed = contenders.named(name)
        if listed is None:
            raise ValueError(f"{name} は試す埋め込みの一覧に無い")
        contenders.register(listed)
        return Multilingual(
            model=listed.source,
            ai_model=listed.ai_model,
            prefixes=listed.prefixes,
            cache_dir=cache_dir,
            source=listed.source,
            declared_gb=listed.declared_gb,
            announcing=announcing,
            runner=runner,
        )

    def multilingual(
        self,
        cache_dir: Path | None,
        announcing: Announcing | None = None,
        runner: Callable[[], Embedder] | None = None,
    ) -> Multilingual:
        """今の多言語の AIモデル。`multilingual` の名前でも、既定が替わった後もこれを指す。

        配布元の置き場と大きさは道具の対応表から取り、支度で取得先を探すのに使う。
        """
        described = Table().described(MODEL)
        return Multilingual(
            model=MODEL,
            ai_model=AI_MODEL,
            cache_dir=cache_dir,
            source=None if described is None else described.source,
            declared_gb=None if described is None else described.declared_gb,
            announcing=announcing,
            runner=runner,
        )
