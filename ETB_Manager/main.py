"""ETB Manager v0.3 — hôte modulaire pour les aides Escape the Backrooms."""
from __future__ import annotations

import argparse
import json
import logging
import math
import mimetypes
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from memory_reader import MemoryReader
from module_system import ModuleRegistry
from level_detector import LevelDetector

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
MODULES_DIR = BASE_DIR / "modules"
MAX_JSON_REQUEST = 64 * 1024
MAX_MODULE_UPLOAD = 512 * 1024 * 1024


def configure_logging() -> Path:
    """Crée un journal persistant pour les démarrages et erreurs du gestionnaire."""
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "etb_manager.log", maxBytes=2_000_000,
        backupCount=4, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        "%Y-%m-%d %H:%M:%S"
    ))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    logging.getLogger("ETB").info("Journalisation initialisée: %s", log_dir)
    return log_dir


class Manager:
    def __init__(self, port: int, fake: bool = False, hz: float = 20.0, fake_level: str = "TopFloor"):
        self.port = int(port)
        self.fake = bool(fake)
        self.hz = max(1.0, float(hz))
        self.fake_level = str(fake_level)
        self.lock = threading.RLock()
        self.memory = {
            "ok": False,
            "status": "En attente du jeu…" if not fake else "Simulation active",
            "pos": None,
            "details": {},
            "level_name": None,
            "car_puzzle": None,
            "updated_at": None,
        }
        self.registry = ModuleRegistry(MODULES_DIR, BASE_DIR, self.port)
        self.level_detector = LevelDetector(self.registry)
        self.reader = None
        self.fake_thread = None
        self.fake_stop = threading.Event()

    def start(self):
        if self.fake:
            self.fake_thread = threading.Thread(target=self._fake_loop, daemon=True, name="ETB-FakeReader")
            self.fake_thread.start()
        else:
            self.reader = MemoryReader(self.on_memory_state, self.on_memory_status, self.hz)
            self.reader.start()

    def on_memory_state(self, data: dict):
        details = data.get("details", {}) or {}
        with self.lock:
            self.memory.update({
                "ok": True,
                "status": "Lecture active — données reçues",
                "pos": {
                    "x": float(data["x"]), "y": float(data["y"]),
                    "z": float(data["z"]), "yaw": float(data["yaw"]),
                },
                "details": details,
                "level_name": str(data.get("level_name") or details.get("world_name") or "").strip() or None,
                "car_puzzle": data.get("car_puzzle"),
                "updated_at": time.time(),
            })

    def on_memory_status(self, message: str, ok: bool = False):
        with self.lock:
            self.memory["status"] = str(message)
            self.memory["ok"] = bool(ok)
            if not ok:
                self.memory["level_name"] = None
                self.memory["car_puzzle"] = None

    def _fake_loop(self):
        t0 = time.perf_counter()
        while not self.fake_stop.is_set():
            a = (time.perf_counter() - t0) * 0.3
            x = 7217 + 1500 * math.cos(a)
            y = 2719 + 1500 * math.sin(a)
            yaw = math.degrees(a) + 90
            is_intercom = self.fake_level.casefold() in {"topfloor", "habitablezonefloor1"}
            car_puzzle = ({
                "active": True, "found": True, "source": "simulation",
                "status": "Exemple simulé — Bleu, Bleu, Rouge, Vert.",
                "manager_name": "BP_Car_Manager_SIMULATION", "cars_needed": 4,
                "colors_values": [2, 2, 0, 1],
                "colors": [
                    {"value": 2, "enum": "Blue", "label": "Bleu"},
                    {"value": 2, "enum": "Blue", "label": "Bleu"},
                    {"value": 0, "enum": "Red", "label": "Rouge"},
                    {"value": 1, "enum": "Green", "label": "Vert"},
                ],
                "correct_colors_count": 4, "read_status": "Simulation", "warning": None,
            } if is_intercom else {
                "active": False, "found": False,
                "status": "Simulation : le niveau TopFloor n'est pas actif.", "colors": [],
            })
            self.on_memory_state({
                "x": x, "y": y, "z": 90.0, "yaw": yaw,
                "details": {
                    "player_position": [x, y, 90.0], "camera_location": [x, y, 150.0],
                    "pitch": 0.0, "yaw": yaw, "roll": 0.0, "fov": 90.0,
                    "pid": 0, "module": "SIMULATION", "slot": "SIMULATION",
                    "world_name": self.fake_level,
                },
                "level_name": self.fake_level,
                "car_puzzle": car_puzzle,
            })
            self.fake_stop.wait(1.0 / self.hz)

    def shared_state(self):
        with self.lock:
            memory = dict(self.memory)
            pos = dict(memory["pos"]) if memory["pos"] else None
            details = dict(memory["details"])
        age = None if memory["updated_at"] is None else max(0.0, time.time() - memory["updated_at"])
        return {
            "memory": {
                "ok": memory["ok"], "status": memory["status"], "position": pos,
                "details": details, "level_name": memory.get("level_name"), "age": age,
            },
            "car_puzzle": memory.get("car_puzzle"),
            "game_level_name": memory.get("level_name"),
        }

    def snapshot(self):
        shared = self.shared_state()
        detection = self.level_detector.detect({
            "pos": shared["memory"]["position"],
            "details": shared["memory"]["details"],
            "level_name": shared["memory"]["level_name"],
            "connected": shared["memory"]["ok"],
        })
        module_states = self.registry.module_state(shared)
        return {
            "module": detection["module"],
            "module_label": detection["label"],
            "level_detection": detection["status"],
            "detected_level": detection,
            "game_level_name": shared["game_level_name"],
            "memory": shared["memory"],
            "car_puzzle": shared["car_puzzle"],
            "modules": self.registry.public_list(),
            "module_state": module_states,
            # Compatibilité douce avec les anciennes versions de l'interface.
            "garage": module_states.get("garage", {}),
            "butterflies": module_states.get("butterflies", {}),
        }

    def module_action(self, module_id: str, action: str, payload: dict):
        return self.registry.action(module_id, action, payload, self.shared_state())

    def module_upload(self, module_id: str, temp_path: Path, filename: str):
        return self.registry.upload(module_id, temp_path, filename, self.shared_state())

    def shutdown(self):
        self.fake_stop.set()
        if self.reader:
            self.reader.stop()
        self.registry.shutdown()


class Handler(BaseHTTPRequestHandler):
    manager: Manager = None

    def _send(self, code: int, body: bytes, content_type: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send(code, body)

    def _json_payload(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_JSON_REQUEST:
            raise ValueError("Requête trop volumineuse.")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw or b"{}")
        if not isinstance(value, dict):
            raise ValueError("Le corps JSON doit être un objet.")
        return value

    def _send_file(self, target: Path):
        body = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix.lower() in {".html", ".js", ".css", ".json", ".svg"}:
            content_type += "; charset=utf-8"
        self._send(200, body, content_type)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/state":
            self._json(200, self.manager.snapshot())
            return
        if path == "/api/modules":
            self._json(200, {"modules": self.manager.registry.public_list()})
            return
        if path == "/api/health":
            self._json(200, {"ok": True, "app": "ETB Manager", "version": "0.3.1"})
            return
        if path == "/pos":
            # API de compatibilité consommée par les cartes live existantes.
            memory = self.manager.shared_state()["memory"]
            data = dict(memory.get("position") or {})
            data.update(status=memory.get("status", ""), ok=memory.get("ok", False), d=memory.get("details", {}))
            if memory.get("age") is not None:
                data["age"] = round(memory["age"], 3)
            self._json(200, data)
            return
        if path.startswith("/modules/"):
            self._serve_module_file(path)
            return
        if path in {"/", "/index.html"}:
            target = WEB_DIR / "index.html"
        elif path in {"/style.css", "/app.js"}:
            target = WEB_DIR / path.lstrip("/")
        else:
            self._json(404, {"error": "Ressource inconnue."})
            return
        try:
            self._send_file(target)
        except OSError:
            self._json(404, {"error": "Fichier d'interface introuvable."})

    def _serve_module_file(self, url_path: str):
        parts = url_path.split("/", 3)
        if len(parts) < 4:
            self._json(404, {"error": "Fichier de module non spécifié."})
            return
        module_id, relative = parts[2], unquote(parts[3])
        definition = self.manager.registry.get(module_id)
        if not definition:
            self._json(404, {"error": "Module inconnu."})
            return

        if module_id == "importcarte" and relative in {"live_map.html", "map.config.json"}:
            backend = self.manager.registry._backends.get(module_id)
            try:
                if relative == "live_map.html":
                    body = backend.read_map()
                    content_type = "text/html; charset=utf-8"
                else:
                    body = backend.read_config()
                    content_type = "application/json; charset=utf-8"
                self._send(200, body, content_type)
            except Exception as exc:
                self._json(404, {"error": str(exc)})
            return

        if module_id == "level0" and relative == "level0_live_map.html":
            candidates = [definition["path"] / "level0_live_map.html", definition["path"] / "public" / "level0_live_map.html"]
            target = next((p for p in candidates if p.is_file()), None)
        elif module_id == "level0" and relative in {"Level0_map_v0.png", "Level0_map_v0.json"}:
            target = self.manager.registry.resolve_public_file(module_id, f"public/{relative}")
        else:
            target = self.manager.registry.resolve_public_file(module_id, relative)
        if target is None:
            self._json(404, {"error": f"Fichier introuvable dans le module '{module_id}'."})
            return
        try:
            self._send_file(target)
        except OSError:
            self._json(404, {"error": "Impossible de lire le fichier du module."})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/modules/"):
                parts = path.strip("/").split("/")
                if len(parts) != 4:
                    self._json(404, {"error": "Route module invalide."})
                    return
                _, _, module_id, endpoint = parts
                if endpoint == "upload":
                    self._handle_module_upload(module_id)
                    return
                payload = self._json_payload()
                if endpoint == "action":
                    action = str(payload.pop("action", ""))
                    result = self.manager.module_action(module_id, action, payload)
                elif endpoint == "region":
                    result = self.manager.module_action(module_id, "set_region", payload)
                else:
                    self._json(404, {"error": "Action de module inconnue."})
                    return
                self._json(200, result)
                return

            # Routes historiques conservées pour les outils déjà installés.
            payload = self._json_payload()
            if path == "/api/action":
                action = str(payload.pop("action", ""))
                result = self.manager.module_action("garage", action, payload)
            elif path == "/api/garage/region":
                result = self.manager.module_action("garage", "set_region", payload)
            elif path == "/api/module":
                requested = str(payload.get("module", "none"))
                if requested != "none" and not self.manager.registry.get(requested):
                    raise ValueError("Module inconnu.")
                result = {"ok": True, "module": requested, "message": "Les changements automatiques restent pilotés par le niveau détecté."}
            else:
                self._json(404, {"error": "Route inconnue."})
                return
            self._json(200, result)
        except Exception as exc:
            self._json(400, {"ok": False, "error": str(exc)})

    def _handle_module_upload(self, module_id: str):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            self._json(400, {"ok": False, "error": "Fichier vide."})
            return
        if length > MAX_MODULE_UPLOAD:
            self._json(413, {"ok": False, "error": "Fichier trop volumineux (maximum 512 Mo)."})
            return
        definition = self.manager.registry.get(module_id)
        if not definition:
            self._json(404, {"ok": False, "error": "Module inconnu."})
            return
        module_dir = definition["path"]
        module_dir.mkdir(parents=True, exist_ok=True)
        filename = unquote(self.headers.get("X-File-Name", "upload.bin"))
        filename = Path(filename).name[:180] or "upload.bin"
        temp_path = module_dir / f".{module_id}.uploading"
        try:
            remaining = length
            with temp_path.open("wb") as handle:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ConnectionError("Transfert interrompu avant la fin du fichier.")
                    handle.write(chunk)
                    remaining -= len(chunk)
            result = self.manager.module_upload(module_id, temp_path, filename)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._json(200 if result.get("ok", True) else 400, result)
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            self._json(400, {"ok": False, "error": str(exc)})

    def log_message(self, *_args):
        pass


def make_handler(manager):
    class BoundHandler(Handler):
        pass
    BoundHandler.manager = manager
    return BoundHandler


def main():
    parser = argparse.ArgumentParser(description="ETB Manager — hôte modulaire pour Escape the Backrooms")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--hz", type=float, default=20.0)
    parser.add_argument("--fake", action="store_true", help="Simule la position du joueur pour tester les modules")
    parser.add_argument("--fake-level", default="TopFloor", help="Nom UWorld simulé avec --fake (ex. TopFloor, Level0, Moths)")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not (WEB_DIR / "index.html").is_file():
        raise SystemExit("Interface web introuvable : le dossier web doit rester à côté de main.py.")
    log_dir = configure_logging()
    logging.getLogger("ETB").info("Démarrage ETB Manager (port=%s, fake=%s)", args.port, args.fake)
    manager = Manager(args.port, fake=args.fake, hz=args.hz, fake_level=args.fake_level)
    logging.getLogger("ETB").info("Modules chargés: %s", manager.registry.ids())
    for module_id, error in manager.registry._errors.items():
        logging.getLogger("ETB").error("Module %s: %s", module_id, error)
        print(f"[Module {module_id}] {error}", flush=True)
    print(f"Journaux disponibles dans : {log_dir}", flush=True)
    print(f"Modules chargés : {', '.join(manager.registry.ids()) or 'aucun'}", flush=True)

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(manager))
    except OSError as exc:
        manager.shutdown()
        raise SystemExit(f"Impossible de démarrer le serveur sur le port {args.port} : {exc}")

    manager.start()
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="ETB-WebServer")
    thread.start()
    url = f"http://127.0.0.1:{args.port}/"
    print(f"ETB Manager v0.3.1 : {url}  (Ctrl+C pour quitter)", flush=True)
    if not args.no_browser:
        webbrowser.open(url)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("Arrêt d'ETB Manager…", flush=True)
    finally:
        manager.shutdown()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
