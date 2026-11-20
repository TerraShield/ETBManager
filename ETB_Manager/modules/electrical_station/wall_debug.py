"""Inspection des exports JSON de meshes FModel pour le debug de carte."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

MAX_WALL_JSON = 30 * 1024 * 1024

def _exports(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("Exports", "exports", "objects", "Objects"):
            if isinstance(data.get(key), list):
                return data[key]
    raise ValueError("Format JSON non reconnu : export FModel attendu (liste Exports).")

def inspect_wall_export(path: Path):
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_WALL_JSON:
        raise ValueError("Le JSON est vide ou dépasse la limite de 30 Mo.")
    data = _exports(json.loads(raw.decode("utf-8-sig")))
    assets = []
    body_setups = [o for o in data if isinstance(o, dict) and o.get("Type") == "BodySetup"]
    for obj in data:
        if not isinstance(obj, dict) or obj.get("Type") != "StaticMesh":
            continue
        name = str(obj.get("Name") or "StaticMesh")
        package = str(obj.get("Package") or "")
        object_path = str(obj.get("ObjectPath") or "")
        outer = obj.get("Outer") if isinstance(obj.get("Outer"), dict) else {}
        outer_path = str(outer.get("ObjectPath") or outer.get("ObjectName") or "")
        full_path = object_path or (f"{package}.{name}" if package else "") or (f"{outer_path}/{name}" if outer_path else name)
        props = obj.get("Properties") or {}
        body_ref = props.get("BodySetup") if isinstance(props, dict) else None
        body_ref_path = body_ref.get("ObjectPath") if isinstance(body_ref, dict) else None
        assets.append({
            "name": name,
            "package": package,
            "object_path": full_path,
            "identity": full_path.casefold(),
            "body_setup_ref": body_ref_path,
            "has_body_setup_export": bool(body_setups),
            "extended_bounds": bool(props.get("ExtendedBounds")) if isinstance(props, dict) else False,
        })
    if not assets:
        raise ValueError("Aucun objet de type StaticMesh trouvé dans ce JSON.")
    return {
        "filename": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "export_count": len(data),
        "body_setup_count": len(body_setups),
        "assets": assets,
    }
