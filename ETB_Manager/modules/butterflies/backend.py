"""Backend autonome du module de cartographie du labyrinthe des papillons."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path


class ButterfliesBackend:
    MAX_UPLOAD = 512 * 1024 * 1024
    MAX_MESH_JSON = 30 * 1024 * 1024

    def __init__(self, context):
        self.root = context.module_dir
        self.builder = self.root / "etb_map_builder.py"
        self.source = self.root / "fmodel_export.json"
        self.mesh_dir = self.root / "mesh" / "imported"
        self.mesh_dir.mkdir(parents=True, exist_ok=True)
        self.output_settings = self.root / ".map_output.json"
        self.output_dir = self.root / "public"
        try:
            saved = json.loads(self.output_settings.read_text(encoding="utf-8"))
            saved_path = Path(saved.get("output_dir", "")).expanduser()
            if str(saved_path).strip():
                self.output_dir = saved_path.resolve()
        except (OSError, ValueError, TypeError):
            pass
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_prefix = self.output_dir / "butterflies_map"
        self.lock = threading.Lock()
        self.map_path = self._find_map()
        self.status = (
            f"Carte prête : {self.map_path.name}." if self.map_path
            else "Importe un export JSON de FModel pour générer la carte."
        )
        self.error = None

    def _find_map(self, newer_than_ns=0):
        candidates = [
            path for path in self.output_dir.glob("*_live_map.html")
            if path.is_file() and path.stat().st_mtime_ns >= newer_than_ns
        ]
        if not candidates:
            return None
        live = [p for p in candidates if "live" in p.stem.casefold()]
        return max(live or candidates, key=lambda p: p.stat().st_mtime_ns)

    def snapshot(self, shared_state):
        if self.map_path and not self.map_path.is_file():
            self.map_path = self._find_map()
        return {
            "map_ready": bool(self.map_path and self.map_path.is_file()),
            "map_name": self.map_path.name if self.map_path and self.map_path.is_file() else None,
            "map_url": "/modules/importcarte/live_map.html" if self.map_path and self.map_path.is_file() else None,
            "output_dir": str(self.output_dir),
            "map_version": self.map_path.stat().st_mtime_ns if self.map_path and self.map_path.is_file() else None,
            "status": self.status,
            "error": self.error,
            "builder_available": self.builder.is_file(),
            "source_available": self.source.is_file(),
            "building": self.lock.locked(),
            "wall_json_files": self._wall_json_catalog(),
        }


    def _wall_json_catalog(self):
        files = []
        for path in sorted(self.mesh_dir.glob("*.json"), key=lambda item: item.name.casefold()):
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(data, list):
                    exports = data
                else:
                    exports = next((data.get(key) for key in ("Exports", "exports", "objects", "Objects") if isinstance(data, dict) and isinstance(data.get(key), list)), [])
                for obj in exports:
                    if not isinstance(obj, dict) or obj.get("Type") != "StaticMesh":
                        continue
                    name = str(obj.get("Name") or "StaticMesh")
                    raw_path = str(obj.get("ObjectPath") or obj.get("Package") or "")
                    if not raw_path and isinstance(obj.get("Outer"), dict):
                        raw_path = str(obj["Outer"].get("ObjectPath") or obj["Outer"].get("ObjectName") or "")
                    files.append({"name": name, "asset_path": raw_path or name, "file": path.name})
            except Exception as exc:
                files.append({"name": path.name, "asset_path": "", "file": path.name, "error": str(exc)})
        by_name = {}
        for asset in files:
            by_name.setdefault(asset["name"].casefold(), []).append(asset)
        duplicates = [{"name": group[0]["name"], "assets": group} for group in by_name.values() if len({item["asset_path"].casefold() for item in group}) > 1]
        return {"files": files, "duplicates": duplicates}

    def _import_wall_json(self, temp_path, filename):
        if Path(filename or "").suffix.casefold() != ".json":
            raise ValueError("Le fichier doit être un export JSON FModel.")
        if not temp_path.is_file() or temp_path.stat().st_size <= 0:
            raise ValueError("Le fichier JSON est vide.")
        if temp_path.stat().st_size > self.MAX_MESH_JSON:
            raise ValueError("Un export de mesh ne peut pas dépasser 30 Mo.")
        data = json.loads(temp_path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            exports = next((data.get(key) for key in ("Exports", "exports", "objects", "Objects") if isinstance(data.get(key), list)), None)
        else:
            exports = data
        if not isinstance(exports, list) or not any(isinstance(obj, dict) and obj.get("Type") == "StaticMesh" for obj in exports):
            raise ValueError("Aucun StaticMesh trouvé dans cet export JSON.")
        self.mesh_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(temp_path.read_bytes()).hexdigest()[:12]
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(filename).stem)[:60] or "wall"
        destination = self.mesh_dir / (stem + "_" + digest + ".json")
        shutil.copy2(temp_path, destination)
        catalog = self._wall_json_catalog()
        count = sum(1 for asset in catalog["files"] if asset["file"] == destination.name)
        return {"ok": True, "message": "Export ajouté : " + destination.name + " (" + str(count) + " mesh).", "wall_json_files": catalog, "asset_count": count}

    def _save_output_dir(self, destination):
        destination = Path(destination).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        self.output_dir = destination
        self.map_path = self._find_map()
        self.output_settings.write_text(
            json.dumps({"output_dir": str(destination)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"ok": True, "message": f"Dossier de sortie : {destination}", "output_dir": str(destination)}

    def action(self, action, payload, shared_state):
        if action == "set_output_dir":
            raw = str(payload.get("output_dir") or "").strip()
            if not raw:
                raise ValueError("Indique un dossier de destination.")
            destination = Path(raw).expanduser()
            if not destination.is_absolute():
                destination = self.root / destination
            return self._save_output_dir(destination)
        if action == "choose_output_dir":
            try:
                import tkinter as tk
                from tkinter import filedialog
                dialog = tk.Tk()
                dialog.withdraw()
                try:
                    dialog.attributes("-topmost", True)
                except Exception:
                    pass
                selected = filedialog.askdirectory(
                    title="Choisir le dossier de destination de la carte",
                    initialdir=str(self.output_dir),
                    parent=dialog,
                )
                dialog.destroy()
            except Exception as exc:
                raise RuntimeError(f"Impossible d'ouvrir le sélecteur de dossier : {exc}") from exc
            if not selected:
                return {"ok": True, "cancelled": True, "message": "Sélection annulée."}
            return self._save_output_dir(selected)
        raise ValueError(f"Action Papillons inconnue : {action}")

    def read_config(self):
        config_dir = self.map_path.parent if self.map_path and self.map_path.is_file() else self.output_dir
        config = config_dir / "map.config.json"
        if not config.is_file():
            raise FileNotFoundError("map.config.json introuvable pour la carte active.")
        return config.read_bytes()

    def upload(self, temp_path: Path, filename: str, shared_state):
        if str(filename or "").startswith("wall__"):
            return self._import_wall_json(temp_path, str(filename)[6:])
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "error": "Une génération de carte est déjà en cours."}
        try:
            if not self.builder.is_file():
                message = "etb_map_builder.py est introuvable dans le dossier du module Papillons."
                self.status, self.error = message, message
                return {"ok": False, "error": message}
            if Path(filename or "export.json").suffix.casefold() != ".json":
                return {"ok": False, "error": "Le fichier doit être un export JSON de FModel."}
            if not temp_path.is_file() or temp_path.stat().st_size <= 0:
                return {"ok": False, "error": "Le fichier JSON reçu est vide."}

            self.root.mkdir(parents=True, exist_ok=True)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(temp_path), str(self.source))
            safe_stem = Path(filename).stem.strip().replace(" ", "_") or "map"
            safe_stem = "".join(char for char in safe_stem if char.isalnum() or char in "-_")[:80] or "map"
            self.output_prefix = self.output_dir / safe_stem
            started_ns = time.time_ns()
            self.status = "Génération de la carte en cours…"
            self.error = None
            command = [
                sys.executable, str(self.builder), str(self.source),
                "--out", str(self.output_prefix),
                "--config-out", str(self.output_dir / "map.config.json"),
                "--no-open",
            ]
            for mesh_file in sorted(self.mesh_dir.glob("*.json")):
                command.extend(["--meshes", str(mesh_file)])
            try:
                completed = subprocess.run(
                    command, cwd=str(self.root), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=600, check=False,
                )
            except subprocess.TimeoutExpired:
                message = "La génération a dépassé 10 minutes et a été arrêtée."
                self.status, self.error = "Échec de génération de la carte.", message
                return {"ok": False, "error": message}

            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "Aucun détail fourni par le générateur.").strip()[-5000:]
                message = f"Échec de génération (code {completed.returncode}).\n{detail}"
                self.status, self.error = "Échec de génération de la carte.", message
                return {"ok": False, "error": message}

            generated = Path(str(self.output_prefix) + "_live_map.html")
            if not generated.is_file() or generated.stat().st_mtime_ns < started_ns:
                generated = self._find_map(started_ns)
            if generated is None:
                message = "Le générateur n'a produit aucun fichier HTML dans le dossier du module."
                self.status, self.error = "HTML généré introuvable.", message
                return {"ok": False, "error": message}

            self.map_path = generated
            self.status = f"Carte générée : {generated.name} — position du joueur synchronisée via ETB Manager."
            self.error = None
            return {"ok": True, "message": self.status, **self.snapshot(shared_state)}
        finally:
            self.lock.release()

    def read_map(self):
        if not self.map_path or not self.map_path.is_file():
            raise FileNotFoundError("La carte du labyrinthe n'a pas encore été générée.")
        return self.map_path.read_bytes()


def create_module(context):
    return ButterfliesBackend(context)
