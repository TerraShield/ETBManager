"""Découverte et cycle de vie des modules ETB Manager.

Un module est un dossier de modules/<id>/ contenant module.json, ui/ et
éventuellement backend.py. Le cœur ne connaît pas les noms des modules.
"""
from __future__ import annotations

import importlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MODULE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,47}$")
PUBLIC_SUFFIXES = {".html", ".htm", ".js", ".css", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".json", ".woff", ".woff2"}


@dataclass(frozen=True)
class ModuleContext:
    module_id: str
    module_dir: Path
    base_dir: Path
    port: int


class ModuleRegistry:
    """Charge les fiches de modules et les backends associés, sans liste codée en dur."""

    def __init__(self, modules_dir: Path, base_dir: Path, port: int):
        self.modules_dir = Path(modules_dir).resolve()
        self.base_dir = Path(base_dir).resolve()
        self.port = int(port)
        self._definitions: dict[str, dict[str, Any]] = {}
        self._backends: dict[str, Any] = {}
        self._errors: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        self._definitions.clear()
        self._backends.clear()
        self._errors.clear()
        self.modules_dir.mkdir(parents=True, exist_ok=True)
        for folder in sorted(self.modules_dir.iterdir(), key=lambda p: p.name.casefold()):
            if not folder.is_dir() or folder.name.startswith("_"):
                continue
            manifest_path = folder / "module.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self._validate_manifest(folder, manifest)
                module_id = manifest["id"]
                if module_id in self._definitions:
                    raise ValueError(f"Identifiant de module dupliqué : {module_id}")
                self._definitions[module_id] = {"manifest": manifest, "path": folder.resolve()}
            except Exception as exc:
                self._errors[folder.name] = f"Fiche module invalide : {exc}"
                logging.getLogger("ETB.Modules").exception("Module %s ignoré: fiche invalide", folder.name)

        # Chargement des backends après validation de toutes les fiches.
        for module_id, definition in self._definitions.items():
            manifest = definition["manifest"]
            backend_name = manifest.get("backend")
            if not backend_name:
                continue
            try:
                if backend_name != "backend.py":
                    raise ValueError("Pour cette version, le backend doit être nommé backend.py")
                backend_path = definition["path"] / backend_name
                if not backend_path.is_file():
                    raise FileNotFoundError(f"{backend_name} introuvable")
                imported = importlib.import_module(f"modules.{module_id}.backend")
                importlib.reload(imported)
                factory = getattr(imported, "create_module", None)
                if not callable(factory):
                    raise TypeError("backend.py doit exposer create_module(context)")
                context = ModuleContext(module_id, definition["path"], self.base_dir, self.port)
                self._backends[module_id] = factory(context)
                logging.getLogger("ETB.Modules").info("Module %s chargé (backend actif=True)", module_id)
            except Exception as exc:
                self._errors[module_id] = f"Backend non chargé : {exc}"
                logging.getLogger("ETB.Modules").exception("Backend du module %s non chargé", module_id)

    @staticmethod
    def _validate_manifest(folder: Path, manifest: Any) -> None:
        if not isinstance(manifest, dict):
            raise TypeError("le contenu doit être un objet JSON")
        module_id = str(manifest.get("id", ""))
        if not MODULE_ID_RE.fullmatch(module_id):
            raise ValueError("id invalide (minuscules, chiffres, tirets et underscores)")
        if folder.name != module_id:
            raise ValueError(f"le dossier doit s'appeler '{module_id}'")
        for key in ("name", "description", "icon"):
            if not isinstance(manifest.get(key), str) or not manifest[key].strip():
                raise ValueError(f"champ '{key}' absent ou vide")
        levels = manifest.get("levels", [])
        if not isinstance(levels, list) or any(not isinstance(x, str) or not x.strip() for x in levels):
            raise ValueError("'levels' doit être une liste de noms de niveaux")
        ui = manifest.get("ui")
        if not isinstance(ui, dict) or not isinstance(ui.get("entry"), str):
            raise ValueError("'ui.entry' doit désigner le fragment HTML du module")
        for key in ("entry", "script", "style"):
            value = ui.get(key)
            if value is not None:
                p = (folder / value).resolve()
                if folder.resolve() not in p.parents or not p.is_file():
                    raise FileNotFoundError(f"fichier UI '{value}' introuvable ou hors du dossier")

    def ids(self) -> list[str]:
        return list(self._definitions)

    def get(self, module_id: str) -> dict[str, Any] | None:
        return self._definitions.get(module_id)

    def levels(self) -> dict[str, list[str]]:
        return {
            module_id: list(definition["manifest"].get("levels", []))
            for module_id, definition in self._definitions.items()
        }

    def public_list(self) -> list[dict[str, Any]]:
        output = []
        for module_id, definition in self._definitions.items():
            manifest = definition["manifest"]
            ui = manifest["ui"]
            output.append({
                "id": module_id,
                "name": manifest["name"],
                "short_name": manifest.get("short_name", manifest["name"]),
                "description": manifest["description"],
                "icon": manifest["icon"],
                "levels": list(manifest.get("levels", [])),
                "order": int(manifest.get("order", 100)),
                "ui": {
                    "entry": ui["entry"],
                    "script": ui.get("script"),
                    "style": ui.get("style"),
                },
                "backend_enabled": bool(manifest.get("backend")),
                "backend_available": (not bool(manifest.get("backend"))) or (module_id in self._backends),
                "error": self._errors.get(module_id),
            })
        return sorted(output, key=lambda item: (item["order"], item["name"].casefold()))

    def module_state(self, shared_state: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for module_id in self._definitions:
            backend = self._backends.get(module_id)
            state: dict[str, Any] = {}
            if backend is not None and callable(getattr(backend, "snapshot", None)):
                try:
                    value = backend.snapshot(shared_state)
                    if isinstance(value, dict):
                        state.update(value)
                except Exception as exc:
                    state["error"] = str(exc)
            entry_error = self._errors.get(module_id)
            if entry_error:
                state["backend_error"] = entry_error
            result[module_id] = state
        return result

    def action(self, module_id: str, action: str, payload: dict[str, Any], shared_state: dict[str, Any]):
        if module_id not in self._definitions:
            raise KeyError(f"Module inconnu : {module_id}")
        backend = self._backends.get(module_id)
        if backend is None:
            raise RuntimeError(f"Le module '{module_id}' ne possède pas de backend actif.")
        handler = getattr(backend, "action", None)
        if not callable(handler):
            raise RuntimeError(f"Le backend '{module_id}' ne gère pas les actions.")
        return handler(action, payload, shared_state)

    def upload(self, module_id: str, temp_path: Path, filename: str, shared_state: dict[str, Any]):
        if module_id not in self._definitions:
            raise KeyError(f"Module inconnu : {module_id}")
        backend = self._backends.get(module_id)
        handler = getattr(backend, "upload", None) if backend else None
        if not callable(handler):
            raise RuntimeError(f"Le module '{module_id}' n'accepte pas de fichier.")
        return handler(temp_path, filename, shared_state)

    def resolve_public_file(self, module_id: str, relative_path: str) -> Path | None:
        definition = self._definitions.get(module_id)
        if not definition:
            return None
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            return None
        candidate = (definition["path"] / relative).resolve()
        root = definition["path"]
        allowed_root = relative.parts[0] in {"ui", "public"}
        legacy_level0_map = module_id == "level0" and relative.as_posix() == "level0_live_map.html"
        if not (allowed_root or legacy_level0_map) or root not in candidate.parents or not candidate.is_file():
            return None
        if candidate.name == "module.json" or candidate.suffix.lower() not in PUBLIC_SUFFIXES:
            return None
        return candidate

    def shutdown(self) -> None:
        for backend in self._backends.values():
            close = getattr(backend, "shutdown", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
