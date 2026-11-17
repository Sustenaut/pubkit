#!/usr/bin/env python3
"""Generate testdata/ from a source independent of the crate.

Replace this with the real independent source for the crate's behaviour:
a specification, another implementation, a published test suite. Each
vector file names its source. Run from the repository root.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES = [("world", "hello, world"), ("publet", "hello, publet")]

lines = ['{', '  "description": "Greetings.",',
         '  "source": "the greeting format, written out by hand",', '  "cases": [']
lines += [f'    {{"input": "{i}", "output": "{o}"}}' + ("," if n < len(CASES) - 1 else "")
          for n, (i, o) in enumerate(CASES)]
lines += ['  ]', '}']
text = "\n".join(lines) + "\n"
json.loads(text)
(ROOT / "testdata").mkdir(exist_ok=True)
(ROOT / "testdata" / "greetings.json").write_text(text)
print("wrote testdata/greetings.json")
