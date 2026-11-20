"""Teste le raccord au générateur avec un faux script, sans modifier le générateur réel."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module_system import ModuleContext
from modules.importcarte.backend import create_module


def main():
    with tempfile.TemporaryDirectory() as temp:
        module_dir = Path(temp) / "importcarte"
        module_dir.mkdir()
        fake_builder = module_dir / "etb_map_builder.py"
        fake_builder.write_text(
            "import sys\nfrom pathlib import Path\n"
            "prefix = Path(sys.argv[sys.argv.index('--out') + 1])\n"
            "Path(str(prefix) + '_live_map.html').write_text('<!doctype html><title>test</title>', encoding='utf-8')\n",
            encoding="utf-8",
        )
        backend = create_module(ModuleContext("importcarte", module_dir, ROOT, 8765))
        upload = Path(temp) / "incoming.json"
        upload.write_text("{\"test\": true}", encoding="utf-8")
        result = backend.upload(upload, "fmodel-test.json", {})
        assert result.get("ok") is True, result
        assert result.get("map_ready") is True, result
        assert backend.read_map().startswith(b"<!doctype html>")

        walls = Path(temp) / "walls.json"
        walls.write_text(
            '[{"Type":"StaticMesh","Name":"SM_Wall_01","ObjectPath":"/Game/Exterior/SM_Wall_01.SM_Wall_01"},'
            '{"Type":"StaticMesh","Name":"SM_Wall_01","ObjectPath":"/Game/Interior/SM_Wall_01.SM_Wall_01"}]',
            encoding="utf-8",
        )
        imported = backend.upload(walls, "wall__walls.json", {})
        assert imported.get("ok") is True, imported
        duplicates = imported["wall_json_files"]["duplicates"]
        assert len(duplicates) == 1 and len(duplicates[0]["assets"]) == 2, duplicates
        assert duplicates[0]["assets"][0]["asset_path"] != duplicates[0]["assets"][1]["asset_path"]

        reset = backend.action("reset_imported_walls", {}, {})
        assert reset.get("ok") is True, reset
        assert reset.get("archived_count") == 1, reset
        backup = Path(reset["backup_dir"])
        assert (backup / imported["wall_json_files"]["files"][0]["file"]).is_file()
        assert backend._wall_json_catalog()["files"] == []
        assert (module_dir / "mesh" / "imported").is_dir()
        print("OK — génération, meshes homonymes et réinitialisation avec sauvegarde des imports.")


if __name__ == "__main__":
    main()
