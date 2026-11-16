"""État du module carte Level 0. La carte live reste un fichier autonome."""
from pathlib import Path


class Level0Backend:
    def __init__(self, context):
        self.root = context.module_dir

    def snapshot(self, shared_state):
        live_candidates = [self.root / "level0_live_map.html", self.root / "public" / "level0_live_map.html"]
        live = next((p for p in live_candidates if p.is_file()), None)
        preview = self.root / "public" / "Level0_map_v0.png"
        return {
            "live_map_available": live is not None,
            "preview_available": preview.is_file(),
            "map_url": "/modules/level0/level0_live_map.html" if live else None,
        }


def create_module(context):
    return Level0Backend(context)
