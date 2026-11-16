from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.boiler_room.backend import BoilerRoomBackend


class Context:
    module_dir = ROOT / "modules" / "boiler_room"


def main():
    backend = BoilerRoomBackend(Context())
    segments = json.loads((Context.module_dir / "public" / "movement_segments.json").read_text(encoding="utf-8"))
    assert len(segments) == 57
    for color, expected in (("#1ae40c", "green"), ("#e48a0c", "orange")):
        segment = next(item for item in segments if item["color"] == color)
        pos = {"x": (segment["x1"] + segment["x2"]) / 2, "y": (segment["y1"] + segment["y2"]) / 2}
        result = backend.snapshot({"memory": {"ok": True, "position": pos}})["movement_hint"]
        assert result["kind"] == expected, (color, result)
    outside = backend.snapshot({"memory": {"ok": True, "position": {"x": 50000, "y": 50000}}})["movement_hint"]
    assert outside["kind"] == "none", outside
    waiting = backend.snapshot({"memory": {"ok": False, "position": None}})["movement_hint"]
    assert waiting["kind"] == "waiting", waiting
    print("OK — 57 segments, consignes vert/orange, distance et attente vérifiés.")


if __name__ == "__main__":
    main()
