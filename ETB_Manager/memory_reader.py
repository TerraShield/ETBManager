"""Lecture mémoire en lecture seule d'Escape the Backrooms (UE4.27, Windows x64)."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import re
import struct
import sys
import threading
import time
from typing import Callable, Optional

PROCESS_NAME = "Backrooms-Win64-Shipping.exe"
GWORLD_AOB = "48 8B 1D ?? ?? ?? ?? 48 85 DB 74 ?? 41 B0 01"
# Signature GNames/FNamePool documentée par le dépôt etb-trainer (UE 4.27).
GNAMES_AOB = "4C 8D 05 ?? ?? ?? ?? EB 16 48 8D 0D ?? ?? ?? ?? E8"

OFF_WORLD_GAME_INSTANCE = 0x180
OFF_GI_LOCAL_PLAYERS = 0x38
OFF_LOCALPLAYER_CONTROLLER = 0x30
OFF_CONTROLLER_PAWN = 0x250
OFF_CONTROLLER_ACKNOWLEDGED_PAWN = 0x2A0
OFF_CONTROLLER_CAMERA_MANAGER = 0x2B8
OFF_ACTOR_ROOT_COMPONENT = 0x130
OFF_SCENE_COMPONENT_TO_WORLD_TRANSLATION = 0x1D0
OFF_CAMERA_CACHE_POV = 0x1AF0
OFF_WORLD_NAME = 0x18

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
IMAGE_SCN_MEM_EXECUTE = 0x20000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class MemoryReader:
    """Lit la position/caméra à intervalle régulier et publie les résultats via callbacks."""

    def __init__(self, on_state: Callable[[dict], None], on_status: Callable[[str, bool], None], hz: float = 20.0):
        self.on_state = on_state
        self.on_status = on_status
        self.hz = max(1.0, float(hz))
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, name="ETB-MemoryReader", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def run(self) -> None:
        if sys.platform != "win32" or ctypes.sizeof(ctypes.c_void_p) != 8:
            self.on_status("Le lecteur mémoire nécessite Windows 64 bits et Python 64 bits.", False)
            return

        mem = None
        slot = None
        gnames = None
        name_resolver = None
        pid = None
        base = 0
        yaw = 0.0
        fails = 0
        last_msg = ""
        last_puzzle_scan = 0.0
        puzzle_state = {
            "active": False,
            "found": False,
            "status": "En attente de Habitable Zone Floor 1…",
            "colors": [],
        }

        def say(msg: str, ok: bool = False) -> None:
            nonlocal last_msg
            self.on_status(msg, ok)
            if msg != last_msg:
                print(msg, flush=True)
                last_msg = msg

        def disconnect() -> None:
            nonlocal mem, slot, gnames, name_resolver, pid, base
            if mem is not None:
                try:
                    mem.close()
                except Exception:
                    pass
            mem = None
            slot = None
            gnames = None
            name_resolver = None
            pid = None
            base = 0

        try:
            while not self.stop_event.is_set():
                try:
                    if mem is None:
                        say(f"Recherche de {PROCESS_NAME}…")
                        new_pid = find_process_id(PROCESS_NAME)
                        new_mem = ProcessMemory(new_pid)
                        try:
                            new_base, _size = find_module(new_pid, PROCESS_NAME)
                            say("Recherche de GWorld dans les sections exécutables…")
                            new_slot = locate_gworld(new_mem, new_base)
                            # GNames est facultatif : son absence ne doit pas bloquer
                            # la lecture de position/caméra qui fonctionnait déjà.
                            try:
                                say("Recherche de GNames (nom du niveau)…")
                                new_gnames = locate_gnames(new_mem, new_base)
                                new_name_resolver = FNameResolver(new_mem, new_gnames)
                            except Exception as name_exc:
                                new_gnames = None
                                new_name_resolver = None
                                print(f"Nom du niveau indisponible : {name_exc}", flush=True)
                        except Exception:
                            new_mem.close()
                            raise
                        mem, pid, base, slot = new_mem, new_pid, new_base, new_slot
                        gnames, name_resolver = new_gnames, new_name_resolver
                        print(f"Processus trouvé : PID {pid}", flush=True)
                        print(f"Module : 0x{base:X}", flush=True)
                        print(f"GWorld trouvé : 0x{slot:X}", flush=True)
                        if gnames:
                            print(f"GNames trouvé : 0x{gnames:X}", flush=True)

                    current_pid = find_process_id(PROCESS_NAME)
                    if current_pid != pid:
                        raise RuntimeError("Le processus du jeu a changé.")

                    st = read_player_state(mem, slot, name_resolver)

                    # Le puzzle des voitures n'est utile que sur TopFloor.
                    # On le lit avec le même handle mémoire, environ une fois par seconde.
                    world_name = st.get("world_name", "?")
                    if world_name.casefold() == "topfloor":
                        now = time.monotonic()
                        if now - last_puzzle_scan >= 1.0:
                            last_puzzle_scan = now
                            if name_resolver is not None:
                                try:
                                    puzzle_state = read_level1_puzzle(
                                        mem, st["world"], name_resolver
                                    )
                                except Exception as puzzle_exc:
                                    puzzle_state = {
                                        "active": True,
                                        "found": False,
                                        "status": f"Lecture du puzzle impossible : {puzzle_exc}",
                                        "colors": [],
                                    }
                            else:
                                puzzle_state = {
                                    "active": True,
                                    "found": False,
                                    "status": "GNames indisponible : impossible de rechercher BP_Car_Manager_C.",
                                    "colors": [],
                                }
                    else:
                        puzzle_state = {
                            "active": False,
                            "found": False,
                            "status": "Ce module attend le niveau TopFloor.",
                            "colors": [],
                        }

                    if st["yaw"] is not None:
                        yaw = st["yaw"]
                    x, y, z = st["player_position"]
                    details = {
                        "player_position": list(st["player_position"]),
                        "camera_location": list(st["camera_location"]) if st["camera_location"] else None,
                        "pitch": st["pitch"], "yaw": st["yaw"], "roll": st["roll"], "fov": st["fov"],
                        "pawn": ptr_str(st["pawn"]), "root_component": ptr_str(st["root_component"]),
                        "camera_manager": ptr_str(st["camera_manager"]), "pid": pid,
                        "module": f"0x{base:X}", "slot": f"0x{slot:X}",
                        "world": ptr_str(st["world"]), "world_name": st["world_name"],
                        "gnames": f"0x{gnames:X}" if gnames else None,
                    }
                    self.on_state({"x": x, "y": y, "z": z, "yaw": yaw,
                                   "level_name": st["world_name"] if st["world_name"] != "?" else None,
                                   "details": details,
                                   "car_puzzle": dict(puzzle_state)})
                    say("Lecture active — données reçues", True)
                    fails = 0
                except Exception as exc:
                    fails += 1
                    say(f"En attente / lecture impossible : {exc}")
                    if fails >= 5:
                        say("Lecture bloquée : reconnexion au jeu…")
                        disconnect()
                        fails = 0
                        self.stop_event.wait(1.0)
                    elif mem is None:
                        self.stop_event.wait(2.0)
                self.stop_event.wait(1.0 / self.hz)
        finally:
            disconnect()


# ---- Win32 / Unreal helpers. Les API ne sont appelées que sous Windows. ----
if sys.platform == "win32":
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:
    kernel32 = None

HANDLE = wt.HANDLE
DWORD = wt.DWORD
BOOL = wt.BOOL
SIZE_T = ctypes.c_size_t


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", DWORD), ("cntUsage", DWORD), ("th32ProcessID", DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", DWORD),
        ("cntThreads", DWORD), ("th32ParentProcessID", DWORD),
        ("pcPriClassBase", ctypes.c_long), ("dwFlags", DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", DWORD), ("th32ModuleID", DWORD), ("th32ProcessID", DWORD),
        ("GlblcntUsage", DWORD), ("ProccntUsage", DWORD),
        ("modBaseAddr", ctypes.c_void_p), ("modBaseSize", DWORD),
        ("hModule", wt.HMODULE), ("szModule", ctypes.c_wchar * 256),
        ("szExePath", ctypes.c_wchar * 260),
    ]


if kernel32 is not None:
    kernel32.CreateToolhelp32Snapshot.argtypes = [DWORD, DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = HANDLE
    kernel32.Process32FirstW.argtypes = [HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = BOOL
    kernel32.Process32NextW.argtypes = [HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = BOOL
    kernel32.Module32FirstW.argtypes = [HANDLE, ctypes.POINTER(MODULEENTRY32W)]
    kernel32.Module32FirstW.restype = BOOL
    kernel32.Module32NextW.argtypes = [HANDLE, ctypes.POINTER(MODULEENTRY32W)]
    kernel32.Module32NextW.restype = BOOL
    kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
    kernel32.OpenProcess.restype = HANDLE
    kernel32.ReadProcessMemory.argtypes = [HANDLE, ctypes.c_void_p, ctypes.c_void_p, SIZE_T, ctypes.POINTER(SIZE_T)]
    kernel32.ReadProcessMemory.restype = BOOL
    kernel32.CloseHandle.argtypes = [HANDLE]
    kernel32.CloseHandle.restype = BOOL


def _require_windows() -> None:
    if kernel32 is None:
        raise RuntimeError("Cette fonction nécessite Windows.")


def win_error(prefix: str) -> OSError:
    _require_windows()
    code = ctypes.get_last_error()
    return OSError(code, f"{prefix} : {ctypes.FormatError(code).strip()}")


def find_process_id(exe_name: str) -> int:
    _require_windows()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        raise win_error("Impossible d'énumérer les processus")
    entry = PROCESSENTRY32W(); entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == exe_name.lower():
                return int(entry.th32ProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    raise RuntimeError(f"{exe_name} introuvable. Lance Escape the Backrooms et entre dans une partie.")


def find_module(pid: int, module_name: str) -> tuple[int, int]:
    _require_windows()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        raise win_error("Impossible d'énumérer les modules du jeu")
    entry = MODULEENTRY32W(); entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel32.Module32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szModule.lower() == module_name.lower():
                return int(entry.modBaseAddr), int(entry.modBaseSize)
            ok = kernel32.Module32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    raise RuntimeError(f"Module {module_name} introuvable dans le processus du jeu.")


class ProcessMemory:
    def __init__(self, pid: int):
        _require_windows()
        self.handle = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not self.handle:
            raise win_error("OpenProcess a échoué (essaie éventuellement de lancer Python avec les mêmes droits que le jeu)")

    def read(self, address: int, size: int) -> Optional[bytes]:
        if not address or address < 0x10000 or address > 0x7FFFFFFFFFFF or size <= 0:
            return None
        buffer = ctypes.create_string_buffer(size)
        bytes_read = SIZE_T(0)
        ok = kernel32.ReadProcessMemory(self.handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(bytes_read))
        if not ok or bytes_read.value != size:
            return None
        return buffer.raw[:size]

    def ptr(self, address: int) -> int:
        data = self.read(address, 8)
        return struct.unpack("<Q", data)[0] if data else 0

    def vec3(self, address: int) -> Optional[tuple[float, float, float]]:
        data = self.read(address, 12)
        if not data:
            return None
        values = struct.unpack("<3f", data)
        return values if all(math.isfinite(v) for v in values) else None

    def close(self) -> None:
        if getattr(self, "handle", None):
            kernel32.CloseHandle(self.handle)
            self.handle = None


def executable_sections(mem: ProcessMemory, module_base: int) -> list[tuple[int, int]]:
    dos = mem.read(module_base, 0x40)
    if not dos or dos[:2] != b"MZ":
        raise RuntimeError("En-tête DOS du jeu illisible : impossible de scanner GWorld.")
    nt_offset = struct.unpack_from("<I", dos, 0x3C)[0]
    nt = module_base + nt_offset
    nt_head = mem.read(nt, 0x18)
    if not nt_head or nt_head[:4] != b"PE\0\0":
        raise RuntimeError("En-tête PE du jeu illisible.")
    section_count = struct.unpack_from("<H", nt_head, 6)[0]
    optional_header_size = struct.unpack_from("<H", nt_head, 0x14)[0]
    section_table = nt + 0x18 + optional_header_size
    found = []
    for index in range(section_count):
        section = mem.read(section_table + index * 40, 40)
        if not section:
            continue
        virtual_size, virtual_address = struct.unpack_from("<II", section, 8)
        raw_size = struct.unpack_from("<I", section, 16)[0]
        characteristics = struct.unpack_from("<I", section, 36)[0]
        size = virtual_size or raw_size
        if characteristics & IMAGE_SCN_MEM_EXECUTE and size:
            found.append((module_base + virtual_address, size))
    if not found:
        raise RuntimeError("Aucune section exécutable trouvée dans l'exécutable du jeu.")
    return found


def compile_aob(pattern: str) -> tuple[re.Pattern[bytes], int]:
    tokens = pattern.split()
    regex_bytes = b"".join(b"." if token == "??" else re.escape(bytes([int(token, 16)])) for token in tokens)
    return re.compile(regex_bytes, re.DOTALL), len(tokens)


def locate_gworld(mem: ProcessMemory, module_base: int) -> int:
    regex, pattern_length = compile_aob(GWORLD_AOB)
    for section_start, section_size in executable_sections(mem, module_base):
        chunk_size = 4 * 1024 * 1024
        for offset in range(0, section_size, chunk_size):
            read_size = min(chunk_size + pattern_length - 1, section_size - offset)
            data = mem.read(section_start + offset, read_size)
            if not data:
                continue
            for match in regex.finditer(data):
                instruction_address = section_start + offset + match.start()
                displacement = struct.unpack_from("<i", data, match.start() + 3)[0]
                candidate = instruction_address + 7 + displacement
                if mem.read(candidate, 8) is not None:
                    return candidate
    raise RuntimeError("Signature AOB de GWorld introuvable. Le jeu a peut-être été mis à jour.")


def locate_gnames(mem: ProcessMemory, module_base: int) -> int:
    """Localise le FNamePool (GNames) à partir de la signature du jeu."""
    regex, pattern_length = compile_aob(GNAMES_AOB)
    for section_start, section_size in executable_sections(mem, module_base):
        chunk_size = 4 * 1024 * 1024
        for offset in range(0, section_size, chunk_size):
            read_size = min(chunk_size + pattern_length - 1, section_size - offset)
            data = mem.read(section_start + offset, read_size)
            if not data:
                continue
            for match in regex.finditer(data):
                instruction_address = section_start + offset + match.start()
                displacement = struct.unpack_from("<i", data, match.start() + 3)[0]
                candidate = instruction_address + 7 + displacement
                # GNames est un FNamePool intégré à cette adresse, pas un pointeur indirect.
                header = mem.read(candidate + 0x8, 8)
                first_block = mem.ptr(candidate + 0x10)
                if header is not None and first_block:
                    current_block, byte_cursor = struct.unpack("<II", header)
                    if current_block < 0x1000 and byte_cursor <= 0x20000:
                        return candidate
    raise RuntimeError("Signature AOB de GNames introuvable. Le jeu a peut-être été mis à jour.")


class FNameResolver:
    """Résout un FName UE4.27 depuis le FNamePool, en lecture mémoire seule."""

    def __init__(self, mem: ProcessMemory, pool_address: int):
        self.mem = mem
        self.pool_address = pool_address
        self.names: dict[int, str] = {}
        self.blocks: dict[int, int] = {}

    def resolve_index(self, index: int) -> str:
        if index in self.names:
            return self.names[index]

        block, offset = index >> 16, index & 0xFFFF
        block_address = self.blocks.get(block)
        if block_address is None:
            block_address = self.mem.ptr(self.pool_address + 0x10 + block * 8)
            if not block_address:
                return "?"
            self.blocks[block] = block_address

        entry_address = block_address + offset * 2
        header_data = self.mem.read(entry_address, 2)
        if not header_data:
            return "?"

        header = struct.unpack("<H", header_data)[0]
        length, is_wide = header >> 6, header & 1
        if length <= 0 or length > 1024:
            return "?"

        byte_length = length * (2 if is_wide else 1)
        raw = self.mem.read(entry_address + 2, byte_length)
        if raw is None:
            return "?"

        value = raw.decode("utf-16-le" if is_wide else "latin-1", "replace")
        self.names[index] = value
        return value

    def object_name(self, object_address: int) -> str:
        """Lit UObject::NamePrivate (+0x18), un FName de 8 octets."""
        data = self.mem.read(object_address + OFF_WORLD_NAME, 8)
        if not data:
            return "?"
        index, number = struct.unpack("<II", data)
        name = self.resolve_index(index)
        if number and name != "?":
            return f"{name}_{number - 1}"
        return name



# ---- Lecture du puzzle couleur de Habitable Zone Floor 1 ----
# Les offsets ci-dessous correspondent aux structures UE4.27 utilisées
# par le scanner indépendant et aux propriétés validées dans le rapport JSON.
OFF_WORLD_PERSISTENT_LEVEL = 0x30
OFF_WORLD_LEVELS = 0x138
OFF_LEVEL_ACTORS = 0x98
OFF_UOBJECT_CLASS = 0x10
OFF_USTRUCT_SUPER = 0x40
OFF_USTRUCT_CHILD_PROPERTIES = 0x50
OFF_FFIELD_NEXT = 0x20
OFF_FFIELD_NAME = 0x28
OFF_FPROPERTY_OFFSET = 0x4C
MAX_PUZZLE_LEVELS = 256
MAX_PUZZLE_ACTORS = 1_000_000
MAX_PUZZLE_COLORS = 32

GARAGE_COLOR_LABELS = {
    0: ("Red", "Rouge"),
    1: ("Green", "Vert"),
    2: ("Blue", "Bleu"),
    3: ("Yellow", "Jaune"),
    4: ("Orange", "Orange"),
    5: ("Purple", "Violet"),
    6: ("Cyan", "Cyan"),
    7: ("Magenta", "Magenta"),
}


def _read_i32(mem: ProcessMemory, address: int) -> Optional[int]:
    data = mem.read(address, 4)
    return struct.unpack("<i", data)[0] if data else None


def _read_unreal_pointer_array(mem: ProcessMemory, data_address: int, count: int, limit: int) -> list[int]:
    if not data_address or count <= 0:
        return []
    count = min(count, limit)
    raw = mem.read(data_address, count * 8)
    if raw is not None and len(raw) == count * 8:
        return [value for value in struct.unpack(f"<{count}Q", raw) if value]
    result = []
    for index in range(count):
        value = mem.ptr(data_address + index * 8)
        if value:
            result.append(value)
    return result


def _read_unreal_tarray_pointers(mem: ProcessMemory, address: int, limit: int) -> list[int]:
    data_address = mem.ptr(address)
    count = _read_i32(mem, address + 8)
    capacity = _read_i32(mem, address + 12)
    if count is None or capacity is None or count < 0 or count > limit or capacity < count:
        return []
    return _read_unreal_pointer_array(mem, data_address, count, limit)


def _read_unreal_tarray_bytes(mem: ProcessMemory, address: int, limit: int) -> tuple[Optional[list[int]], str]:
    data_address = mem.ptr(address)
    count = _read_i32(mem, address + 8)
    capacity = _read_i32(mem, address + 12)
    if count is None or capacity is None:
        return None, "En-tête TArray illisible"
    if count < 0 or count > limit or capacity < count or capacity < 0:
        return None, f"Dimensions TArray incohérentes (Num={count}, Max={capacity})"
    if count == 0:
        return [], "Tableau vide"
    if not data_address:
        return None, "Pointeur Data nul alors que Num > 0"
    raw = mem.read(data_address, count)
    if raw is None or len(raw) != count:
        return None, "Données du TArray illisibles"
    return list(raw), "OK"


def _read_fname_at(mem: ProcessMemory, resolver: FNameResolver, address: int) -> str:
    data = mem.read(address, 8)
    if not data:
        return "?"
    index, number = struct.unpack("<II", data)
    name = resolver.resolve_index(index)
    if number and name != "?":
        return f"{name}_{number - 1}"
    return name


def _find_unreal_property_offset(mem: ProcessMemory, resolver: FNameResolver,
                                 class_address: int, property_name: str) -> Optional[int]:
    """Résout Offset_Internal depuis les FProperty de la classe et de ses parents."""
    current_class = class_address
    visited_classes: set[int] = set()
    while current_class and current_class not in visited_classes:
        visited_classes.add(current_class)
        field = mem.ptr(current_class + OFF_USTRUCT_CHILD_PROPERTIES)
        visited_fields: set[int] = set()
        field_steps = 0
        while field and field not in visited_fields and field_steps < 4096:
            visited_fields.add(field)
            field_steps += 1
            field_name = _read_fname_at(mem, resolver, field + OFF_FFIELD_NAME)
            if field_name == property_name:
                offset = _read_i32(mem, field + OFF_FPROPERTY_OFFSET)
                if offset is not None and 0 <= offset < 0x10000:
                    return offset
            field = mem.ptr(field + OFF_FFIELD_NEXT)
        current_class = mem.ptr(current_class + OFF_USTRUCT_SUPER)
    return None


def read_level1_puzzle(mem: ProcessMemory, world: int, resolver: FNameResolver) -> dict:
    """Lit CorrectColors du BP_Car_Manager_C actif sur TopFloor, sans écrire en mémoire."""
    level_addresses: list[int] = []
    persistent = mem.ptr(world + OFF_WORLD_PERSISTENT_LEVEL)
    if persistent:
        level_addresses.append(persistent)
    level_addresses.extend(_read_unreal_tarray_pointers(mem, world + OFF_WORLD_LEVELS, MAX_PUZZLE_LEVELS))
    level_addresses = list(dict.fromkeys(address for address in level_addresses if address))

    manager_candidates = []
    seen_actors: set[int] = set()
    for level_address in level_addresses:
        actors = _read_unreal_tarray_pointers(mem, level_address + OFF_LEVEL_ACTORS, MAX_PUZZLE_ACTORS)
        for actor in actors:
            if actor in seen_actors:
                continue
            seen_actors.add(actor)
            class_address = mem.ptr(actor + OFF_UOBJECT_CLASS)
            if not class_address:
                continue
            class_name = resolver.object_name(class_address)
            if class_name.casefold() != "bp_car_manager_c":
                continue

            colors_offset = _find_unreal_property_offset(mem, resolver, class_address, "CorrectColors")
            cars_offset = _find_unreal_property_offset(mem, resolver, class_address, "CarsNeeded")
            if colors_offset is None:
                color_values, read_status = None, "FProperty CorrectColors introuvable"
            else:
                color_values, read_status = _read_unreal_tarray_bytes(
                    mem, actor + colors_offset, MAX_PUZZLE_COLORS
                )
                read_status = f"{read_status} (propriété à +0x{colors_offset:X})"

            cars_needed = _read_i32(mem, actor + cars_offset) if cars_offset is not None else None
            manager_name = resolver.object_name(actor)
            manager_candidates.append({
                "manager_name": manager_name,
                "manager_class": class_name,
                "cars_needed": cars_needed,
                "colors_values": color_values,
                "read_status": read_status,
            })

    if not manager_candidates:
        return {
            "active": True,
            "found": False,
            "status": "TopFloor détecté, mais BP_Car_Manager_C est introuvable dans les niveaux chargés.",
            "cars_needed": None,
            "colors": [],
            "manager_name": None,
        }

    # Préférer le gestionnaire dont le tableau a exactement CarsNeeded éléments.
    def candidate_score(item):
        values = item["colors_values"]
        needed = item["cars_needed"]
        exact = values is not None and needed is not None and len(values) == needed and needed > 0
        has_values = values is not None and len(values) > 0
        return (exact, has_values, len(values or []))

    selected = max(manager_candidates, key=candidate_score)
    values = selected["colors_values"]
    colors = []
    for value in values or []:
        enum_name, french_name = GARAGE_COLOR_LABELS.get(value, (f"Unknown_{value}", f"Valeur inconnue ({value})"))
        colors.append({"value": value, "enum": enum_name, "label": french_name})

    needed = selected["cars_needed"]
    if values is None:
        status = f"Gestionnaire trouvé ({selected['manager_name']}), mais CorrectColors est illisible."
    elif not values:
        status = f"Gestionnaire trouvé ({selected['manager_name']}), mais CorrectColors est vide."
    elif needed is not None and len(values) != needed:
        status = f"Combinaison lue partiellement : {len(values)} couleur(s) pour {needed} voiture(s)."
    else:
        status = "Combinaison lue depuis BP_Car_Manager_C.CorrectColors."

    return {
        "active": True,
        "found": True,
        "status": status,
        "manager_name": selected["manager_name"],
        "manager_class": selected["manager_class"],
        "cars_needed": needed,
        "colors_values": values,
        "colors": colors,
        "correct_colors_count": len(values) if values is not None else None,
        "read_status": selected["read_status"],
        "warning": (
            f"Le tableau contient {len(values)} valeur(s), mais CarsNeeded vaut {needed}."
            if values is not None and needed is not None and len(values) != needed else None
        ),
        "source": "memory",
    }

def read_player_state(mem: ProcessMemory, gworld_slot: int, name_resolver: Optional[FNameResolver] = None) -> dict:
    world = mem.ptr(gworld_slot)
    if not world:
        raise RuntimeError("GWorld est nul : attends d'être dans une partie.")
    game_instance = mem.ptr(world + OFF_WORLD_GAME_INSTANCE)
    if not game_instance:
        raise RuntimeError("GameInstance introuvable (chargement en cours ?).")
    local_players_data = mem.ptr(game_instance + OFF_GI_LOCAL_PLAYERS)
    if not local_players_data:
        raise RuntimeError("LocalPlayers introuvable.")
    local_player = mem.ptr(local_players_data)
    if not local_player:
        raise RuntimeError("LocalPlayers[0] introuvable.")
    player_controller = mem.ptr(local_player + OFF_LOCALPLAYER_CONTROLLER)
    if not player_controller:
        raise RuntimeError("PlayerController introuvable.")
    pawn = mem.ptr(player_controller + OFF_CONTROLLER_PAWN) or mem.ptr(player_controller + OFF_CONTROLLER_ACKNOWLEDGED_PAWN)
    if not pawn:
        raise RuntimeError("Pawn du joueur introuvable (personnage pas encore possédé ?).")
    root_component = mem.ptr(pawn + OFF_ACTOR_ROOT_COMPONENT)
    if not root_component:
        raise RuntimeError("RootComponent du joueur introuvable.")
    player_position = mem.vec3(root_component + OFF_SCENE_COMPONENT_TO_WORLD_TRANSLATION)
    if player_position is None:
        raise RuntimeError("Position du joueur illisible ou invalide.")
    camera_manager = mem.ptr(player_controller + OFF_CONTROLLER_CAMERA_MANAGER)
    camera_location = None
    pitch = yaw = roll = fov = None
    if camera_manager:
        pov_data = mem.read(camera_manager + OFF_CAMERA_CACHE_POV, 0x1C)
        if pov_data:
            values = struct.unpack("<7f", pov_data)
            candidate_location = values[0:3]
            candidate_pitch, candidate_yaw, candidate_roll, candidate_fov = values[3:7]
            if all(math.isfinite(v) for v in values) and 1.0 < candidate_fov < 179.0 and -3600.0 < candidate_yaw < 3600.0:
                camera_location = candidate_location
                pitch, yaw, roll, fov = candidate_pitch, candidate_yaw, candidate_roll, candidate_fov
    # Le nom de UWorld change avec la map chargée (ex. nom d'asset / de niveau).
    world_name = name_resolver.object_name(world) if name_resolver else "?"

    return {
        "world": world, "world_name": world_name,
        "game_instance": game_instance, "player_controller": player_controller,
        "pawn": pawn, "root_component": root_component, "player_position": player_position,
        "camera_manager": camera_manager, "camera_location": camera_location,
        "pitch": pitch, "yaw": yaw, "roll": roll, "fov": fov,
    }


def ptr_str(value: int) -> Optional[str]:
    return f"0x{value:X}" if value else None
