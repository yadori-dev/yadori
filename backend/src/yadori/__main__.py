"""`python -m yadori` の入口。"""

import sys

from yadori.infrastructure.entry import Entry

sys.exit(Entry(sys.argv).run())
