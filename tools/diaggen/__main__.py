"""`python -m tools.diaggen`: the generator of the UDS jobs (diaggen.py) and the ODX/PDX export (TOOL-43).

  python -m tools.diaggen odx <project.yaml> -o <file.odx-d>   one diagnostic layer container
  python -m tools.diaggen pdx <project.yaml> -o <file.pdx>     PDX package (index.xml and the .odx-d)
  python -m tools.diaggen <project.yaml> <out> [...]           generator, as tools/diaggen/diaggen.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if len(sys.argv) > 1 and sys.argv[1] in ("odx", "pdx"):
    import odxexport  # noqa: E402

    sys.exit(odxexport.main(sys.argv[1:]))

import diaggen  # noqa: E402

diaggen.main()
