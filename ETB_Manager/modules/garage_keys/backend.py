"""Scanner mémoire autonome des BP_KeyPiece_C du Garage (lecture seule)."""
from __future__ import annotations
import math
import struct
import sys
import threading
import time
from memory_reader import (
    PROCESS_NAME, ProcessMemory, find_process_id, find_module,
    locate_gworld, locate_gnames, FNameResolver, read_player_state,
    OFF_WORLD_PERSISTENT_LEVEL, OFF_WORLD_LEVELS, OFF_LEVEL_ACTORS,
    OFF_UOBJECT_CLASS, OFF_ACTOR_ROOT_COMPONENT,
    OFF_SCENE_COMPONENT_TO_WORLD_TRANSLATION, _read_unreal_tarray_pointers,
)

MAX_LEVELS = 256
MAX_ACTORS = 1_000_000
KEY_CLASS = "bp_keypiece_c"
GARAGE_WORLD_NAMES = ("garage", "floor4", "level2")


def _read_keys(mem, world, resolver, player_position):
    levels = []
    persistent = mem.ptr(world + OFF_WORLD_PERSISTENT_LEVEL)
    if persistent:
        levels.append(persistent)
    levels.extend(_read_unreal_tarray_pointers(mem, world + OFF_WORLD_LEVELS, MAX_LEVELS))
    levels = list(dict.fromkeys(x for x in levels if x))
    found, seen = [], set()
    for level in levels:
        for actor in _read_unreal_tarray_pointers(mem, level + OFF_LEVEL_ACTORS, MAX_ACTORS):
            if actor in seen:
                continue
            seen.add(actor)
            class_ptr = mem.ptr(actor + OFF_UOBJECT_CLASS)
            if not class_ptr or resolver.object_name(class_ptr).casefold() != KEY_CLASS:
                continue
            root = mem.ptr(actor + OFF_ACTOR_ROOT_COMPONENT)
            if not root:
                continue
            pos = mem.vec3(root + OFF_SCENE_COMPONENT_TO_WORLD_TRANSLATION)
            if pos is None:
                continue
            dx, dy, dz = (pos[i] - player_position[i] for i in range(3))
            found.append({"id": f"{actor:X}", "x": round(pos[0], 1), "y": round(pos[1], 1),
                          "z": round(pos[2], 1), "distance": round(math.sqrt(dx*dx+dy*dy+dz*dz), 1)})
    found.sort(key=lambda item: item["distance"])
    return found, len(levels), len(seen)


class GarageKeysBackend:
    def __init__(self, context):
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.state = {"active": False, "status": "En attente d'Escape the Backrooms…",
                      "world_name": None, "keys": [], "count": 0, "last_scan": None,
                      "player_position": None, "level_count": 0, "actor_count": 0}
        self.thread = threading.Thread(target=self._run, name="GarageKeysScanner", daemon=True)
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
                        self._set(active=False, status=f"Recherche de {PROCESS_NAME}…", keys=[], count=0)
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
                    if not any(token in world_name.casefold() for token in GARAGE_WORLD_NAMES):
                        self._set(active=False, status=f"Niveau détecté : {world_name}. En attente du Garage…",
                                  world_name=world_name, keys=[], count=0, last_scan=time.time(), player_position=None)
                        failures = 0
                        self.stop_event.wait(1.0)
                        continue
                    player = read_player_state(mem, slot, resolver)
                    keys, level_count, actor_count = _read_keys(mem, world, resolver, player["player_position"])
                    self._set(active=True, status=(f"{len(keys)} clé(s) repérée(s)." if keys else
                                  "Aucune BP_KeyPiece_C trouvée dans les niveaux chargés."),
                                  world_name=world_name, keys=keys, count=len(keys), last_scan=time.time(),
                                  player_position=list(player["player_position"]), level_count=level_count,
                                  actor_count=actor_count)
                    failures = 0
                except Exception as exc:
                    failures += 1
                    self._set(active=False, status=f"Lecture en attente : {exc}")
                    if failures >= 4:
                        if mem is not None:
                            try: mem.close()
                            except Exception: pass
                        mem, resolver = None, None
                        failures = 0
                    self.stop_event.wait(1.0)
                    continue
                self.stop_event.wait(1.0)
        finally:
            if mem is not None:
                try: mem.close()
                except Exception: pass

    def snapshot(self, shared_state):
        with self.lock:
            return dict(self.state, keys=[dict(item) for item in self.state["keys"]])

    def action(self, action, payload, shared_state):
        if action == "refresh":
            return {"ok": True, "message": "Actualisation automatique active."}
        raise ValueError(f"Action Garage Keys inconnue : {action}")

    def shutdown(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)


def create_module(context):
    return GarageKeysBackend(context)
