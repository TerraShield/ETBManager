"""Teste le raccord au générateur avec un faux script, sans modifier le générateur réel."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module_system import ModuleContext
from modules.butterflies.backend import create_module


def main():
    with tempfile.TemporaryDirectory() as temp:
        module_dir = Path(temp) / "butterflies"
        module_dir.mkdir()
        fake_builder = module_dir / "etb_map_builder.py"
        fake_builder.write_text(
            "import sys\nfrom pathlib import Path\n"
            "prefix = Path(sys.argv[sys.argv.index('--out') + 1])\n"
            "Path(str(prefix) + '_live.html').write_text('<!doctype html><title>test</title>', encoding='utf-8')\n",
            encoding="utf-8",
        )
        backend = create_module(ModuleContext("butterflies", module_dir, ROOT, 8765))
        upload = Path(temp) / "incoming.json"
        upload.write_text("{\"test\": true}", encoding="utf-8")
        result = backend.upload(upload, "fmodel-test.json", {})
        assert result.get("ok") is True, result
        assert result.get("map_ready") is True, result
        assert backend.read_map().startswith(b"<!doctype html>")
        print("OK — import brut et détection du HTML généré par le backend Papillons.")


if __name__ == "__main__":
    main()
