import json
from pathlib import Path

# The shared cases live in the monorepo's packages/comfy/fixtures (the JS side runs them too); the
# node pack's own repo carries a synced copy in tests/fixtures (tools/sync-fixtures.mjs).
_MONOREPO = Path(__file__).resolve().parents[2] / "packages" / "comfy" / "fixtures"
FIXTURES = _MONOREPO if _MONOREPO.is_dir() else Path(__file__).resolve().parent / "fixtures"


def fx(name):
    return json.loads((FIXTURES / name).read_text())
