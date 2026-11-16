"""Lecture en mémoire du mot de passe des BP_PadLock_C de Hotel Terror Floor3."""
from __future__ import annotations

import math
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
    read_player_state,
    _find_unreal_property_offset,
    _read_i32,
    _read_unreal_tarray_pointers,
    OFF_WORLD_PERSISTENT_LEVEL,
    OFF_WORLD_LEVELS,
    OFF_LEVEL_ACTORS,
    OFF_UOBJECT_CLASS,
)

TARGET_CODES = {"17564", "05938", "89472"}
MAX_LEVELS = 256
MAX_ACTORS = 1_000_000
MAX_PASSWORD_LENGTH = 16


def _read_fstring(mem: ProcessMemory, address: int) -> str | None:
    """Lit un FString UE4 (TArray de TCHAR UTF-16) situé à address."""
    data_ptr = mem.ptr(address)
    count = _read_i32(mem, address + 8)
    capacity = _read_i32(mem, address + 12)
    if count is None or capacity is None or count < 0 or count > 64 or capacity < count:
        return None
    if count == 0:
        return ""
    if not data_ptr:
        return None
    raw = mem.read(data_ptr, count * 2)
    if raw is None:
        return None
    try:
        return raw.decode("utf-16-le", "replace").split("\x00", 1)[0]
    except Exception:
        return None


def _read_password_array(mem: ProcessMemory, actor: int, offset: int) -> tuple[str | None, str]:
    """Lit TArray<FString> Password et reconstitue les cinq chiffres."""
    address = actor + offset
    data_ptr = mem.ptr(address)
    count = _read_i32(mem, address + 8)
    capacity = _read_i32(mem, address + 12)
    if count is None or capacity is None:
        return None, "En-tête du tableau Password illisible."
    if count < 1 or count > MAX_PASSWORD_LENGTH or capacity < count or not data_ptr:
        return None, f"Tableau Password incohérent (Num={count}, Max={capacity})."

    # Un FString UE4 occupe 16 octets : pointeur Data, Num, Max.
    raw = mem.read(data_ptr, count * 16)
    if raw is None:
        return None, "Éléments FString illisibles."
    chars = []
    for index in range(count):
        item_address = data_ptr + index * 16
        value = _read_fstring(mem, item_address)
        if value is None or len(value) != 1:
            return None, f"Caractère {index + 1}/{count} illisible."
        chars.append(value)

    password = "".join(chars)
    if len(password) != 5 or not password.isdigit():
        return password, f"Valeur lue mais format inattendu : {password!r}"
    return password, "Code lu depuis BP_PadLock_C.Password."


def _read_padlocks(mem: ProcessMemory, world: int, resolver: FNameResolver,
                   player_position: tuple[float, float, float]) -> tuple[list[dict], int, int]:
    levels = []
    persistent = mem.ptr(world + OFF_WORLD_PERSISTENT_LEVEL)
    if persistent:
        levels.append(persistent)
    levels.extend(_read_unreal_tarray_pointers(mem, world + OFF_WORLD_LEVELS, MAX_LEVELS))
    levels = list(dict.fromkeys(value for value in levels if value))

    locks = []
    seen: set[int] = set()
    for level in levels:
        for actor in _read_unreal_tarray_pointers(mem, level + OFF_LEVEL_ACTORS, MAX_ACTORS):
            if actor in seen:
                continue
            seen.add(actor)
            class_address = mem.ptr(actor + OFF_UOBJECT_CLASS)
            if not class_address or resolver.object_name(class_address).casefold() != "bp_padlock_c":
                continue

            password_offset = _find_unreal_property_offset(mem, resolver, class_address, "Password")
            if password_offset is None:
                continue
            password, read_status = _read_password_array(mem, actor, password_offset)
            if password not in TARGET_CODES:
                continue

            root = mem.ptr(actor + 0x130)
            position = mem.vec3(root + 0x1D0) if root else None
            if position is None:
                distance = None
                coords = None
            else:
                dx, dy, dz = (position[i] - player_position[i] for i in range(3))
                distance = round(math.sqrt(dx * dx + dy * dy + dz * dz), 1)
                coords = [round(value, 1) for value in position]

            locks.append({
                "id": f"{actor:X}",
                "code": password,
                "x": coords[0] if coords else None,
                "y": coords[1] if coords else None,
                "z": coords[2] if coords else None,
                "distance": distance,
                "read_status": read_status,
                "property_offset": f"+0x{password_offset:X}",
            })

    locks.sort(key=lambda item: item["distance"] if item["distance"] is not None else float("inf"))
    return locks, len(levels), len(seen)


class HotelTerrorCodeBackend:
    def __init__(self, context):
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.state = {
            "active": False,
            "status": "En attente d'Escape the Backrooms…",
            "world_name": None,
            "code": None,
            "locks": [],
            "count": 0,
            "last_scan": None,
            "player_position": None,
            "level_count": 0,
            "actor_count": 0,
        }
        self.thread = threading.Thread(target=self._run, name="HotelTerrorCodeScanner", daemon=True)
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
                        self._set(active=False, status=f"Recherche de {PROCESS_NAME}…",
                                  code=None, locks=[], count=0)
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

                    player = read_player_state(mem, slot, resolver)
                    world_name = player["world_name"]
                    if world_name.casefold() != "floor3":
                        self._set(
                            active=False,
                            status=f"Niveau détecté : {world_name}. En attente de Hotel Terror Floor3…",
                            world_name=world_name,
                            code=None, locks=[], count=0, last_scan=time.time(),
                            player_position=list(player["player_position"]),
                        )
                        failures = 0
                        self.stop_event.wait(1.0)
                        continue

                    locks, level_count, actor_count = _read_padlocks(
                        mem, player["world"], resolver, player["player_position"]
                    )
                    if locks:
                        selected = locks[0]
                        status = f"Code détecté : {selected['code']}."
                        code = selected["code"]
                    else:
                        status = "Floor3 détecté, mais aucun BP_PadLock_C avec l'un des trois codes attendus n'a été trouvé."
                        code = None

                    self._set(
                        active=True, status=status, world_name=world_name,
                        code=code, locks=locks, count=len(locks), last_scan=time.time(),
                        player_position=list(player["player_position"]),
                        level_count=level_count, actor_count=actor_count,
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
            return dict(self.state, locks=[dict(item) for item in self.state["locks"]])

    def action(self, action, payload, shared_state):
        if action == "refresh":
            return {"ok": True, "message": "Actualisation automatique active."}
        raise ValueError(f"Action Hotel Terror inconnue : {action}")

    def shutdown(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)


def create_module(context):
    return HotelTerrorCodeBackend(context)
