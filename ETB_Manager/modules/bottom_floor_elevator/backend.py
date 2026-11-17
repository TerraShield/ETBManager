"""Détection de l'étage de la porte verrouillée de l'ascenseur dans garageLevel2."""
from __future__ import annotations

import struct
import sys
import threading
import time
import json
from pathlib import Path

from memory_reader import (
    PROCESS_NAME,
    ProcessMemory,
    FNameResolver,
    find_process_id,
    find_module,
    locate_gworld,
    locate_gnames,
    OFF_WORLD_PERSISTENT_LEVEL,
    OFF_WORLD_LEVELS,
    OFF_LEVEL_ACTORS,
    OFF_UOBJECT_CLASS,
    OFF_ACTOR_ROOT_COMPONENT,
    OFF_SCENE_COMPONENT_TO_WORLD_TRANSLATION,
    _find_unreal_property_offset,
    _read_unreal_tarray_pointers,
)

MAX_LEVELS = 256
MAX_ACTORS = 1_000_000
TARGET_WORLD = "garagelevel2"
DOOR_CLASS = "bp_fun_single_door_c"

# Coordonnées relevées approximativement depuis la caméra dans UMap.
# La tolérance XY absorbe le décalage caméra/acteur ; Z distingue les étages.
DOOR_LOCATIONS = (
    {"floor": "Étage 1", "x": -2015.0, "y": 6103.0, "z": 2842.0},
    {"floor": "Étage 2", "x": 1279.0, "y": 6294.0, "z": 2286.0},
    {"floor": "Étage 3", "x": 2140.0, "y": 10165.0, "z": 1741.0},
)
MAX_HORIZONTAL_DISTANCE = 450.0
MAX_VERTICAL_DISTANCE = 300.0
DIAGNOSTIC_RADIUS_XY = 1200.0
DIAGNOSTIC_RADIUS_Z = 700.0
MAX_DIAGNOSTIC_OBJECTS_PER_TARGET = 30
LOG_PATH = Path(__file__).with_name('garage_level2_door_scan.log')


def _read_bool(mem, address: int) -> bool | None:
    data = mem.read(address, 1)
    return bool(data[0]) if data else None


def _read_doors(mem, world, resolver):
    """Inspecte les portes connues ET tous les acteurs proches de leurs repères."""
    levels = []
    persistent = mem.ptr(world + OFF_WORLD_PERSISTENT_LEVEL)
    if persistent:
        levels.append(persistent)
    levels.extend(_read_unreal_tarray_pointers(mem, world + OFF_WORLD_LEVELS, MAX_LEVELS))
    levels = list(dict.fromkeys(value for value in levels if value))

    actors_seen = set()
    actors = []
    for level_index, level in enumerate(levels):
        for actor in _read_unreal_tarray_pointers(mem, level + OFF_LEVEL_ACTORS, MAX_ACTORS):
            if actor in actors_seen:
                continue
            actors_seen.add(actor)
            class_address = mem.ptr(actor + OFF_UOBJECT_CLASS)
            if not class_address:
                continue
            class_name = resolver.object_name(class_address)
            root = mem.ptr(actor + OFF_ACTOR_ROOT_COMPONENT)
            position = mem.vec3(root + OFF_SCENE_COMPONENT_TO_WORLD_TRANSLATION) if root else None
            if position is None:
                continue
            actors.append({
                "address": actor, "id": format(actor, 'X'),
                "class": class_name, "name": resolver.object_name(actor),
                "x": position[0], "y": position[1], "z": position[2],
                "level_index": level_index, "class_address": class_address,
            })

    property_cache = {}
    def inspect_properties(actor):
        class_address = actor["class_address"]
        if class_address not in property_cache:
            property_cache[class_address] = {
                name: _find_unreal_property_offset(mem, resolver, class_address, name)
                for name in ("IsLocked", "IsOpen", "CanCollide")
            }
        offsets = property_cache[class_address]
        return {name: {
            "offset": ("+0x%X" % offset) if offset is not None else None,
            "value": _read_bool(mem, actor["address"] + offset) if offset is not None else None,
        } for name, offset in offsets.items()}

    door_candidates = []
    diagnostics = []
    for location in DOOR_LOCATIONS:
        nearby = []
        for actor in actors:
            dx, dy, dz = actor["x"] - location["x"], actor["y"] - location["y"], actor["z"] - location["z"]
            horizontal, vertical = (dx * dx + dy * dy) ** 0.5, abs(dz)
            if horizontal <= DIAGNOSTIC_RADIUS_XY and vertical <= DIAGNOSTIC_RADIUS_Z:
                props = inspect_properties(actor)
                nearby.append({
                    "id": actor["id"], "name": actor["name"], "class": actor["class"],
                    "x": round(actor["x"], 1), "y": round(actor["y"], 1), "z": round(actor["z"], 1),
                    "dx": round(dx, 1), "dy": round(dy, 1), "dz": round(dz, 1),
                    "horizontal_distance": round(horizontal, 1),
                    "distance": round((horizontal * horizontal + dz * dz) ** 0.5, 1),
                    "properties": props, "has_is_locked": props["IsLocked"]["offset"] is not None,
                })
                if actor["class"].casefold() == DOOR_CLASS:
                    door_candidates.append((location, actor, horizontal, vertical, props))
        nearby.sort(key=lambda item: item["distance"])
        diagnostics.append({
            "floor": location["floor"],
            "reference": {k: location[k] for k in ("x", "y", "z")},
            "radius_xy": DIAGNOSTIC_RADIUS_XY, "radius_z": DIAGNOSTIC_RADIUS_Z,
            "objects_total": len(nearby), "objects": nearby[:MAX_DIAGNOSTIC_OBJECTS_PER_TARGET],
        })

    matched, used = [], set()
    for location in DOOR_LOCATIONS:
        nearby_doors = []
        for ref, actor, horizontal, vertical, props in door_candidates:
            if ref is not location or actor["id"] in used:
                continue
            if horizontal <= MAX_HORIZONTAL_DISTANCE and vertical <= MAX_VERTICAL_DISTANCE:
                nearby_doors.append((horizontal * horizontal + (actor["z"] - location["z"]) ** 2, horizontal, vertical, actor, props))
        if not nearby_doors:
            continue
        _distance_sq, horizontal, vertical, actor, props = min(nearby_doors, key=lambda item: item[0])
        used.add(actor["id"])
        matched.append({
            "id": actor["id"], "floor": location["floor"], "name": actor["name"], "class": actor["class"],
            "is_locked": props["IsLocked"]["value"], "is_open": props["IsOpen"]["value"],
            "can_collide": props["CanCollide"]["value"], "z": round(actor["z"], 1),
            "distance": round((horizontal * horizontal + vertical * vertical) ** 0.5, 1),
        })
    return matched, len(levels), len(actors_seen), len(door_candidates), diagnostics

class GarageLevel2ElevatorBackend:
    def __init__(self, context):
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.state = {
            "active": False,
            "status": "En attente d'Escape the Backrooms…",
            "world_name": None,
            "floor": None,
            "door_count": 0,
            "locked_count": 0,
            "is_open": None,
            "can_collide": None,
            "z": None,
            "distance": None,
            "doors": [],
            "last_scan": None,
            "level_count": 0,
            "actor_count": 0,
            "door_actor_count": 0,
            "diagnostics": [],
            "log_path": str(LOG_PATH),
        }
        self.thread = threading.Thread(
            target=self._run, name="GarageLevel2ElevatorScanner", daemon=True
        )
        self.thread.start()

    def _set(self, **values):
        with self.lock:
            self.state.update(values)

    def _run(self):
        if sys.platform != "win32" or struct.calcsize("P") != 8:
            self._set(status="Le scanner nécessite Windows 64 bits et Python 64 bits.")
            return

        mem = None
        pid = base = slot = 0
        resolver = None
        failures = 0
        try:
            while not self.stop_event.is_set():
                try:
                    if mem is None:
                        self._set(
                            active=False,
                            status=f"Recherche de {PROCESS_NAME}…",
                            floor=None, doors=[], door_count=0, locked_count=0,
                            is_open=None, can_collide=None, z=None, distance=None,
                        )
                        pid = find_process_id(PROCESS_NAME)
                        mem = ProcessMemory(pid)
                        try:
                            base, _ = find_module(pid, PROCESS_NAME)
                            slot = locate_gworld(mem, base)
                            resolver = FNameResolver(mem, locate_gnames(mem, base))
                        except Exception:
                            mem.close()
                            mem = None
                            raise

                    if find_process_id(PROCESS_NAME) != pid:
                        raise RuntimeError("Le processus du jeu a changé.")

                    world = mem.ptr(slot)
                    if not world:
                        raise RuntimeError("GWorld est nul : attends le chargement d'une partie.")
                    world_name = resolver.object_name(world)

                    if world_name.casefold() != TARGET_WORLD:
                        self._set(
                            active=False,
                            status=f"Niveau détecté : {world_name}. En attente de garageLevel2…",
                            world_name=world_name, floor=None, doors=[], door_count=0,
                            locked_count=0, is_open=None, can_collide=None,
                            z=None, distance=None, last_scan=time.time(),
                        )
                        failures = 0
                        self.stop_event.wait(1.0)
                        continue

                    doors, level_count, actor_count, door_actor_count, diagnostics = _read_doors(mem, world, resolver)
                    locked = [door for door in doors if door["is_locked"] is True]
                    unreadable = [door for door in doors if door["is_locked"] is None]

                    selected = locked[0] if len(locked) == 1 else None
                    if len(locked) == 1:
                        status = f"Porte verrouillée détectée : {selected['floor']}."
                    elif len(locked) > 1:
                        status = f"{len(locked)} portes surveillées sont verrouillées : résultat ambigu."
                    elif unreadable:
                        status = "Portes repérées, mais la propriété IsLocked est illisible."
                    elif len(doors) < len(DOOR_LOCATIONS):
                        status = f"{len(doors)}/3 portes repérées. En attente des portes manquantes…"
                    else:
                        status = "Les trois portes sont repérées, mais aucune n'est verrouillée."

                    self._write_diagnostic_log(world_name, doors, diagnostics, level_count, actor_count, door_actor_count)
                    self._set(
                        active=True,
                        status=status,
                        world_name=world_name,
                        floor=selected["floor"] if selected else None,
                        door_count=len(doors),
                        locked_count=len(locked),
                        is_open=selected["is_open"] if selected else None,
                        can_collide=selected["can_collide"] if selected else None,
                        z=selected["z"] if selected else None,
                        distance=selected["distance"] if selected else None,
                        doors=doors,
                        last_scan=time.time(),
                        level_count=level_count,
                        actor_count=actor_count,
                        door_actor_count=door_actor_count,
                        diagnostics=diagnostics,
                    )
                    failures = 0
                except Exception as exc:
                    failures += 1
                    self._set(active=False, status=f"Lecture en attente : {exc}", diagnostics=[])
                    if failures >= 4:
                        if mem is not None:
                            try:
                                mem.close()
                            except Exception:
                                pass
                        mem, resolver = None, None
                        failures = 0
                    self.stop_event.wait(1.0)
                    continue
                self.stop_event.wait(1.0)
        finally:
            if mem is not None:
                try:
                    mem.close()
                except Exception:
                    pass

    def _write_diagnostic_log(self, world_name, doors, diagnostics, level_count, actor_count, door_actor_count):
        """Écrit un journal lisible uniquement quand le résultat du scan change."""
        payload = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "world": world_name,
            "levels": level_count, "actors_scanned": actor_count,
            "door_class_actors": door_actor_count, "matched_doors": doors,
            "nearby_objects": diagnostics,
        }
        signature = json.dumps({key: value for key, value in payload.items() if key != "timestamp"}, sort_keys=True, ensure_ascii=False)
        if signature == getattr(self, "_last_log_signature", None):
            return
        self._last_log_signature = signature
        try:
            with LOG_PATH.open("a", encoding="utf-8") as log:
                log.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        except OSError as exc:
            self._set(log_error=str(exc))
    def snapshot(self, shared_state):
        with self.lock:
            return dict(self.state, doors=[dict(item) for item in self.state["doors"]])

    def action(self, action, payload, shared_state):
        if action == "refresh":
            return {"ok": True, "message": "Actualisation automatique active."}
        raise ValueError(f"Action Garage Elevator inconnue : {action}")

    def shutdown(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)


def create_module(context):
    return GarageLevel2ElevatorBackend(context)
