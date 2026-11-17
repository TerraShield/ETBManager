"""Détection de l'étage de l'ascenseur du Garage depuis garageLevel2."""
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
ELEVATOR_CLASS = "bp_utility_elevator_c"


def _read_fname(mem, resolver, address: int) -> str | None:
    data = mem.read(address, 8)
    if not data:
        return None
    index, number = struct.unpack("<II", data)
    name = resolver.resolve_index(index)
    if not name or name == "?":
        return None
    return f"{name}_{number - 1}" if number else name


def _display_level(name: str | None) -> str | None:
    if not name:
        return None
    value = name.strip()
    if value.casefold() in {"none", "null", "none_name"}:
        return None
    # Les noms Unreal sont du type GarageLevel_2 ou garageLevel2.
    if value.casefold() == "garagelevel2":
        return "Garage Level 2"
    if value.casefold().startswith("garagelevel_"):
        suffix = value.rsplit("_", 1)[-1]
        return f"Étage {suffix}"
    return value.replace("_", " ")


def _read_elevators(mem, world, resolver):
    levels = []
    persistent = mem.ptr(world + OFF_WORLD_PERSISTENT_LEVEL)
    if persistent:
        levels.append(persistent)
    levels.extend(_read_unreal_tarray_pointers(mem, world + OFF_WORLD_LEVELS, MAX_LEVELS))
    levels = list(dict.fromkeys(value for value in levels if value))

    elevators = []
    seen = set()
    for level in levels:
        for actor in _read_unreal_tarray_pointers(mem, level + OFF_LEVEL_ACTORS, MAX_ACTORS):
            if actor in seen:
                continue
            seen.add(actor)
            class_address = mem.ptr(actor + OFF_UOBJECT_CLASS)
            if not class_address or resolver.object_name(class_address).casefold() != ELEVATOR_CLASS:
                continue

            class_name = resolver.object_name(class_address)
            from_offset = _find_unreal_property_offset(mem, resolver, class_address, "FromLevel")
            to_offset = _find_unreal_property_offset(mem, resolver, class_address, "ToLevel")
            from_level = _read_fname(mem, resolver, actor + from_offset) if from_offset is not None else None
            to_level = _read_fname(mem, resolver, actor + to_offset) if to_offset is not None else None

            root = mem.ptr(actor + OFF_ACTOR_ROOT_COMPONENT)
            position = mem.vec3(root + OFF_SCENE_COMPONENT_TO_WORLD_TRANSLATION) if root else None
            elevators.append({
                "id": f"{actor:X}",
                "from_level": from_level,
                "to_level": to_level,
                "floor": _display_level(from_level),
                "z": round(position[2], 1) if position else None,
                "class": class_name,
            })

    # Priorité aux ascenseurs dont la destination déclarée est garageLevel2.
    matching = [item for item in elevators if (item["to_level"] or "").casefold() == "garagelevel2"]
    return matching or elevators, len(levels), len(seen)


class GarageLevel2ElevatorBackend:
    def __init__(self, context):
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.state = {
            "active": False,
            "status": "En attente d'Escape the Backrooms…",
            "world_name": None,
            "floor": None,
            "from_level": None,
            "to_level": None,
            "z": None,
            "elevator_count": 0,
            "elevators": [],
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
                            floor=None, from_level=None, to_level=None, z=None,
                            elevators=[], elevator_count=0,
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
                            world_name=world_name, floor=None, from_level=None,
                            to_level=None, z=None, elevators=[], elevator_count=0,
                            last_scan=time.time(),
                        )
                        failures = 0
                        self.stop_event.wait(1.0)
                        continue

                    elevators, level_count, actor_count = _read_elevators(mem, world, resolver)
                    if not elevators:
                        status = "garageLevel2 détecté, mais aucun BP_Utility_Elevator_C n'est présent dans les niveaux chargés."
                        selected = None
                    else:
                        distinct_floors = {item["from_level"] for item in elevators if item["from_level"]}
                        if len(distinct_floors) == 1:
                            source_level = next(iter(distinct_floors))
                            selected = next(item for item in elevators if item["from_level"] == source_level)
                            floor = selected["floor"]
                            status = f"Ascenseur localisé : {floor or source_level}."
                        elif len(distinct_floors) > 1:
                            selected = None
                            status = f"{len(elevators)} ascenseurs détectés avec plusieurs étages possibles."
                        else:
                            selected = None
                            status = "Ascenseur détecté, mais FromLevel est illisible. La hauteur Z est disponible pour diagnostic."
                    self._set(
                        active=True,
                        status=status,
                        world_name=world_name,
                        floor=selected["floor"] if selected else None,
                        from_level=selected["from_level"] if selected else None,
                        to_level=selected["to_level"] if selected else None,
                        z=selected["z"] if selected else None,
                        elevators=elevators,
                        elevator_count=len(elevators),
                        last_scan=time.time(),
                        level_count=level_count,
                        actor_count=actor_count,
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
            return dict(self.state, elevators=[dict(item) for item in self.state["elevators"]])

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
