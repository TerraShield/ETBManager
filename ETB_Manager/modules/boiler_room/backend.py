from __future__ import annotations
import json
import math


def point_segment_distance(px, py, segment):
    ax, ay = float(segment["x1"]), float(segment["y1"])
    bx, by = float(segment["x2"]), float(segment["y2"])
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px-ax)*dx + (py-ay)*dy) / length_sq))
    return math.hypot(px - (ax+t*dx), py - (ay+t*dy))


def segment_kind(color):
    value = str(color or "").lower()
    if value == "#1ae40c": return "green"
    if value in {"#e1890e", "#e78d0d", "#e68c0f", "#e48a0c", "#e38a0d"}: return "orange"
    try:
        r, g, b = (int(value[i:i+2], 16) for i in (1, 3, 5))
        if g > r * 1.2 and g > b * 1.2: return "green"
        if r > b * 1.5 and g > b * 1.5: return "orange"
    except (ValueError, IndexError):
        pass
    return None


class BoilerRoomBackend:
    PROXIMITY_RADIUS = 250.0

    def __init__(self, context):
        self.root = context.module_dir
        path = self.root / "public" / "movement_segments.json"
        self.segments = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []

    def snapshot(self, shared_state):
        memory = shared_state.get("memory", {}) or {}
        position = memory.get("position")
        hint = {"kind":"waiting", "distance":None, "label":"En attente de la position du joueur"}
        if memory.get("ok") and position and self.segments:
            px, py = float(position["x"]), float(position["y"])
            candidates = [(point_segment_distance(px, py, seg), segment_kind(seg.get("color"))) for seg in self.segments]
            candidates = [(d, kind) for d, kind in candidates if kind]
            if candidates:
                distance, kind = min(candidates, key=lambda item: item[0])
                if distance <= self.PROXIMITY_RADIUS:
                    hint = {"kind":kind, "distance":round(distance,1), "label":"MARCHE / COURSE" if kind == "green" else "ACCROUPIS-TOI"}
                else:
                    hint = {"kind":"none", "distance":round(distance,1), "label":"Aucun tracé à proximité"}
        map_path = self.root / "public" / "BoilerRoom_live_map.html"
        return {"map_available":map_path.is_file(), "map_url":"/modules/boiler_room/public/BoilerRoom_live_map.html" if map_path.is_file() else None,
                "movement_hint":hint, "segments_count":len(self.segments), "proximity_radius":self.PROXIMITY_RADIUS}


def create_module(context):
    return BoilerRoomBackend(context)
