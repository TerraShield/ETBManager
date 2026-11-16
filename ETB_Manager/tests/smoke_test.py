from __future__ import annotations

import json
import sys
import threading
import time
import tempfile
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from level_detector import LevelDetector
from module_system import ModuleRegistry
from main import Manager, make_handler


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_registry_and_level_aliases():
    registry = ModuleRegistry(ROOT / "modules", ROOT, 8765)
    expected = {"level0", "level1", "garage", "butterflies", "boiler_room"}
    check(set(registry.ids()) == expected, f"Modules trouvés : {registry.ids()}")
    check(not registry._errors, f"Erreurs modules : {registry._errors}")
    detector = LevelDetector(registry)
    aliases = {
        "Level0": "level0",
        "TopFloor": "level1",
        "Floor4": "garage",
        "GarageLevel2": "garage",
        "Moths": "butterflies",
        "BoilerRoom": "boiler_room",
        "UnknownTestLevel": "none",
    }
    for world, wanted in aliases.items():
        detector = LevelDetector(registry)
        state = {"connected": True, "level_name": world, "details": {}}
        detector.detect(state)
        result = detector.detect(state)
        check(result["module"] == wanted, f"{world}: attendu {wanted}, reçu {result['module']}")
    registry.shutdown()


def test_new_module_is_discovered_without_core_changes():
    with tempfile.TemporaryDirectory() as temp:
        modules_dir = Path(temp) / "modules"
        module_dir = modules_dir / "test_helper"
        (module_dir / "ui").mkdir(parents=True)
        (module_dir / "ui" / "index.html").write_text("<div>Module test</div>", encoding="utf-8")
        manifest = {
            "id": "test_helper", "name": "Module test", "description": "Vérification de la découverte.",
            "icon": "◇", "levels": ["WorldAddedForTest"], "order": 999,
            "ui": {"entry": "ui/index.html"}
        }
        (module_dir / "module.json").write_text(json.dumps(manifest), encoding="utf-8")
        registry = ModuleRegistry(modules_dir, Path(temp), 8765)
        check("test_helper" in registry.ids(), "Un nouveau dossier module.json n'a pas été découvert")
        detector = LevelDetector(registry)
        player = {"connected": True, "level_name": "WorldAddedForTest", "details": {}}
        detector.detect(player)
        check(detector.detect(player)["module"] == "test_helper", "L'alias ajouté dans module.json n'est pas routé")
        registry.shutdown()


def test_http_host():
    manager = Manager(0, fake=True, hz=10, fake_level="Moths")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(manager))
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager.start()
    base = f"http://127.0.0.1:{port}"
    try:
        time.sleep(0.3)
        with urllib.request.urlopen(base + "/api/modules", timeout=3) as response:
            modules = json.loads(response.read())["modules"]
        check(len(modules) == 5, f"Catalogue incomplet : {len(modules)}")
        with urllib.request.urlopen(base + "/api/state", timeout=3) as response:
            json.loads(response.read())  # première confirmation de transition
        with urllib.request.urlopen(base + "/api/state", timeout=3) as response:
            state = json.loads(response.read())
        check(state["module"] == "butterflies", f"Détection Moths incorrecte : {state['module']}")
        for module_id in ("garage", "level0", "level1", "butterflies", "boiler_room"):
            with urllib.request.urlopen(base + f"/modules/{module_id}/ui/index.html", timeout=3) as response:
                check(response.status == 200 and len(response.read()) > 100, f"Interface {module_id} inaccessible")
        with urllib.request.urlopen(base + "/modules/level0/public/Level0_map_v0.png", timeout=3) as response:
            check(response.status == 200 and response.headers.get("Content-Type", "").startswith("image/png"), "Aperçu Level 0 inaccessible")
        with urllib.request.urlopen(base + "/modules/level0/Level0_map_v0.png", timeout=3) as response:
            check(response.status == 200, "Alias compatibilité image Level 0 inaccessible")
        with urllib.request.urlopen(base + "/modules/boiler_room/public/BoilerRoom_live_map.html", timeout=3) as response:
            boiler_html = response.read()
            check(response.status == 200 and b"const LIVE = true;" in boiler_html, "Carte Boiler Room live inaccessible")
        with urllib.request.urlopen(base + "/modules/boiler_room/public/movement_segments.json", timeout=3) as response:
            segments = json.loads(response.read())
            check(len(segments) == 57, f"Segments Boiler Room incomplets : {len(segments)}")
        with urllib.request.urlopen(base + "/pos", timeout=3) as response:
            pos = json.loads(response.read())
        check(pos.get("ok") is True and "x" in pos, "Route de compatibilité /pos invalide")
        req = urllib.request.Request(
            base + "/api/modules/garage/action",
            data=json.dumps({"action": "players", "value": "2"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            action = json.loads(response.read())
        check(action.get("ok") is True, "Action standard du module Garage invalide")
        # Vérifie aussi le routage automatique vers l'interphone et sa donnée simulée.
        manager.fake_level = "TopFloor"
        time.sleep(0.35)
        with urllib.request.urlopen(base + "/api/state", timeout=3) as response:
            json.loads(response.read())
        with urllib.request.urlopen(base + "/api/state", timeout=3) as response:
            intercom = json.loads(response.read())
        check(intercom["module"] == "level1", "Routage automatique TopFloor invalide")
        check(intercom["car_puzzle"].get("found") is True, "Données de simulation interphone absentes")
    finally:
        manager.shutdown()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    test_registry_and_level_aliases()
    test_new_module_is_discovered_without_core_changes()
    test_http_host()
    print("OK — registre, ajout d’un module factice, détection, API locale et ressources UI vérifiés.")
