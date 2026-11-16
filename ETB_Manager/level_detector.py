"""Associe le nom UWorld au module déclaré dans son module.json."""
from __future__ import annotations

import re
import threading
from typing import Any


def normalize_world_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name or name == "?":
        return ""
    name = re.sub(r"^UEDPIE_\d+_", "", name, flags=re.IGNORECASE)
    name = name.rsplit("/", 1)[-1]
    return name.casefold()


class LevelDetector:
    """Filtre les transitions fugaces et utilise les alias du registre de modules."""

    def __init__(self, registry, confirmations: int = 2):
        self.registry = registry
        self.confirmations = max(1, int(confirmations))
        self._lock = threading.Lock()
        self._candidate = None
        self._candidate_count = 0
        self._current_module = "none"
        self._current_world = None
        self._lookup: dict[str, str] = {}
        self.reload_aliases()

    def reload_aliases(self):
        lookup = {}
        for module_id, aliases in self.registry.levels().items():
            for alias in aliases:
                normalized = normalize_world_name(alias)
                if normalized:
                    if normalized in lookup and lookup[normalized] != module_id:
                        raise ValueError(f"Alias de niveau ambigu '{alias}' : {lookup[normalized]} et {module_id}")
                    lookup[normalized] = module_id
        with self._lock:
            self._lookup = lookup
            self._candidate = None
            self._candidate_count = 0

    def detect(self, player_state: dict) -> dict[str, Any]:
        details = player_state.get("details") or {}
        world_name = (
            player_state.get("level_name")
            or player_state.get("world_name")
            or details.get("world_name")
            or ""
        )
        normalized = normalize_world_name(world_name)
        connected = player_state.get("connected", True)

        if not connected or not normalized:
            with self._lock:
                self._candidate = None
                self._candidate_count = 0
                self._current_module = "none"
                self._current_world = None
            return {
                "module": "none", "label": "Accueil",
                "subtitle": "En attente de la détection automatique du niveau.",
                "status": "En attente du jeu ou du nom du niveau…",
                "world_name": None, "recognized": False,
            }

        target_module = self._lookup.get(normalized, "none")
        with self._lock:
            candidate_key = (target_module, normalized)
            if candidate_key == self._candidate:
                self._candidate_count += 1
            else:
                self._candidate = candidate_key
                self._candidate_count = 1
            stable = self._candidate_count >= self.confirmations
            if stable:
                self._current_module = target_module
                self._current_world = str(world_name)
            module = self._current_module
            stable_world = self._current_world

        if not stable:
            current = self.registry.get(module) if module != "none" else None
            return {
                "module": module,
                "label": current["manifest"]["name"] if current else "Accueil",
                "subtitle": f"Changement de niveau détecté : {world_name}",
                "status": f"Changement de niveau détecté : {world_name} — vérification…",
                "world_name": str(world_name), "recognized": False, "pending": True,
            }

        if target_module == "none":
            return {
                "module": "none",
                "label": "Aucune aide" if stable_world else "Accueil",
                "subtitle": (f"Le niveau {stable_world} est détecté, mais aucun module ne lui est associé."
                             if stable_world else "En attente de la détection automatique du niveau."),
                "status": f"Aucune aide configurée pour : {stable_world or world_name}",
                "world_name": str(world_name), "recognized": False,
            }

        definition = self.registry.get(target_module)
        manifest = definition["manifest"] if definition else {}
        return {
            "module": target_module,
            "label": manifest.get("name", target_module),
            "subtitle": manifest.get("description", "Module ETB."),
            "status": f"Niveau reconnu : {manifest.get('name', target_module)} ({world_name})",
            "world_name": str(world_name), "recognized": True,
        }
