# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

import pathlib
import sys

# Ensure both `src/` (for `import charm`) and the package root are importable.
_SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
