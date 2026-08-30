"""`python -m yadori` の入口。"""

import sys

from yadori.infrastructure.start import Startup

sys.exit(Startup().run())
