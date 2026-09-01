"""文章を数値の並びへ変える実装と、名前で選ぶ部品。"""

from yadori.adapter.embedding.characters import CharacterPairs, Closeness
from yadori.adapter.embedding.choosing import Choosing, NotAnEmbeddingName
from yadori.adapter.embedding.contenders import Contender, Contenders
from yadori.adapter.embedding.default import DefaultEmbeddings
from yadori.adapter.embedding.multilingual import Announcing, Embedder, Multilingual
from yadori.adapter.embedding.table import Described, Table
from yadori.adapter.embedding.weighing import Prepared, Preparing, Size, Weighing, Weight

__all__ = [
    "Announcing",
    "CharacterPairs",
    "Choosing",
    "Closeness",
    "Contender",
    "Contenders",
    "DefaultEmbeddings",
    "Described",
    "Embedder",
    "Multilingual",
    "NotAnEmbeddingName",
    "Prepared",
    "Preparing",
    "Size",
    "Table",
    "Weighing",
    "Weight",
]
