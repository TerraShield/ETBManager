"""Détection de l'étage de la porte verrouillée de l'ascenseur dans garageLevel2."""
from __future__ import annotations

import struct
import sys
import threading
import time

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


def _read_bool(mem, address: int) -> bool | None:
    data = mem.read(address, 1)
    return bool(data[0]) if data else None


def _read_doors(mem, world, resolver):
    levels = []
    persistent = mem.ptr(world + OFF_WORLD_PERSISTENT_LEVEL)
    if persistent:
        levels.append(persistent)
    levels.extend(_read_unreal_tarray_pointers(mem, world + OFF_WORLD_LEVELS, MAX_LEVELS))
    levels = list(dict.fromkeys(value for value in levels if value))

    candidates = []
    seen = set()
    for level in levels:
        for actor in _read_unreal_tarray_pointers(mem, level + OFF_LEVEL_ACTORS, MAX_ACTORS):
            if actor in seen:
                continue
            seen.add(actor)
            class_address = mem.ptr(actor + OFF_UOBJECT_CLASS)
            if not class_address or resolver.object_name(class_address).casefold() != DOOR_CLASS:
                continue

            root = mem.ptr(actor + OFF_ACTOR_ROOT_COMPONENT)
            position = mem.vec3(root + OFF_SCENE_COMPONENT_TO_WORLD_TRANSLATION) if root else None
            if position is None:
                continue

            locked_offset = _find_unreal_property_offset(mem, resolver, class_address, "IsLocked")
            open_offset = _find_unreal_property_offset(mem, resolver, class_address, "IsOpen")
            collide_offset = _find_unreal_property_offset(mem, resolver, class_address, "CanCollide")
            candidates.append({
                "id": f"{actor:X}",
                "x": position[0],
                "y": position[1],
                "z": position[2],
                "is_locked": _read_bool(mem, actor + locked_offset) if locked_offset is not None else None,
                "is_open": _read_bool(mem, actor + open_offset) if open_offset is not None else None,
                "can_collide": _read_bool(mem, actor + collide_offset) if collide_offset is not None else None,
            })

    matched = []
    used = set()
    for location in DOOR_LOCATIONS:
        nearby = []
        for door in candidates:
            if door["id"] in used:
                continue
            dx = door["x"] - location["x"]
            dy = door["y"] - location["y"]
            dz = door["z"] - location["z"]
            horizontal = (dx * dx + dy * dy) ** 0.5
            vertical = abs(dz)
            if horizontal <= MAX_HORIZONTAL_DISTANCE and vertical <= MAX_VERTICAL_DISTANCE:
                nearby.append((horizontal * horizontal + dz * dz, horizontal, vertical, door))
        if not nearby:
            continue
        _distance_sq, horizontal, vertical, door = min(nearby, key=lambda item: item[0])
        used.add(door["id"])
        matched.append({
            "id": door["id"],
            "floor": location["floor"],
            "is_locked": door["is_locked"],
            "is_open": door["is_open"],
            "can_collide": door["can_collide"],
            "z": round(door["z"], 1),
            "distance": round((horizontal * horizontal + vertical * vertical) ** 0.5, 1),
        })

    return matched, len(levels), len(seen), len(candidates)


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

                    doors, level_count, actor_count, door_actor_count = _read_doors(mem, world, resolver)
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
                    )
                    failures = 0
                except Exception as exc:
                    failures += 1
                    self._set(active=False, status=f"Lecture en attente : {exc}")
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
