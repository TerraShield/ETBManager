"""Backend autonome du module de cartographie du labyrinthe des papillons."""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path


class ButterfliesBackend:
    MAX_UPLOAD = 512 * 1024 * 1024

    def __init__(self, context):
        self.root = context.module_dir
        self.builder = self.root / "etb_map_builder.py"
        self.source = self.root / "fmodel_export.json"
        self.output_prefix = self.root / "butterflies_map"
        self.lock = threading.Lock()
        self.map_path = self._find_map()
        self.status = (
            f"Carte prête : {self.map_path.name}." if self.map_path
            else "Importe un export JSON de FModel pour générer la carte."
        )
        self.error = None

    def _find_map(self, newer_than_ns=0):
        candidates = [
            path for path in self.root.glob("butterflies_map*.html")
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
            "map_url": "/modules/butterflies/live_map.html" if self.map_path and self.map_path.is_file() else None,
            "map_version": self.map_path.stat().st_mtime_ns if self.map_path and self.map_path.is_file() else None,
            "status": self.status,
            "error": self.error,
            "builder_available": self.builder.is_file(),
            "source_available": self.source.is_file(),
            "building": self.lock.locked(),
        }

    def upload(self, temp_path: Path, filename: str, shared_state):
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
            shutil.move(str(temp_path), str(self.source))
            started_ns = time.time_ns()
            self.status = "Génération de la carte en cours…"
            self.error = None
            command = [
                sys.executable, str(self.builder), str(self.source),
                "--out", str(self.output_prefix), "--no-open",
            ]
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

            generated = self._find_map(started_ns)
            if generated is None:
                candidates = [p for p in self.root.glob("*.html") if p.is_file() and p.stat().st_mtime_ns >= started_ns]
                if candidates:
                    live = [p for p in candidates if "live" in p.stem.casefold()]
                    generated = max(live or candidates, key=lambda p: p.stat().st_mtime_ns)
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
