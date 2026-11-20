from __future__ import annotations
import json
import sys
import tempfile
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.electrical_station.backend import ElectricalStationScanner


def main():
    with tempfile.TemporaryDirectory() as temp:
        scanner = ElectricalStationScanner.__new__(ElectricalStationScanner)
        scanner.root = Path(temp)
        scanner.mesh_library_path = Path(temp) / "mesh_library" / "static_meshes.json"
        records = [
            {"actor":"Wall_A","mesh":"SM_Wall_01","asset_path":"/Game/Exterior/SM_Wall_01",
             "local_bounds":{"origin":[0,0,0],"extent":[20,300,150]},
             "x":100.0,"y":200.0,"z":150.0,"half_x":20.0,"half_y":300.0,"half_z":150.0,"yaw":0.0},
            {"actor":"Wall_B","mesh":"SM_Wall_01","asset_path":"/Game/Interior/SM_Wall_01",
             "local_bounds":{"origin":[0,0,0],"extent":[20,300,150]},
             "x":800.0,"y":200.0,"z":150.0,"half_x":20.0,"half_y":300.0,"half_z":150.0,"yaw":0.0},
        ]
        count = scanner._write_auto_map(records, "ElectricalStation")
        assert count == 2
        public = Path(temp) / "public"
        svg = public / "electrical_station_auto_map.svg"
        ET.parse(svg)
        data = json.loads((public / "auto_map_data.json").read_text(encoding="utf-8"))
        assert len(data["wallCandidates"]) == 2
        assert data["wallCandidates"][0]["asset_path"] != data["wallCandidates"][1]["asset_path"]
        config = json.loads((public / "map.config.json").read_text(encoding="utf-8"))
        assert config["source"] == "runtime_static_mesh_scan"
        assert config["worldBounds"]["maxX"] > config["worldBounds"]["minX"]
        library = json.loads(scanner.mesh_library_path.read_text(encoding="utf-8"))
        assert len(library["assets"]) == 2
        assert library["identity"] == "full_asset_path"
    print("OK — carte SVG, bounds monde et assets homonymes conservés.")


if __name__ == "__main__":
    main()