"""Backend du module BottomFloor : diagnostic mémoire des lampadaires."""
from __future__ import annotations
from .lamp_memory import LampMemoryScanner

class GarageBackend:
    def __init__(self, context): self.scanner = LampMemoryScanner()
    def snapshot(self, shared_state): return self.scanner.snapshot()
    def action(self, action, payload, shared_state):
        if action == "start": return self.scanner.start()
        if action == "stop": return self.scanner.stop()
        if action == "clear": return self.scanner.clear()
        if action == "players": return self.scanner.set_players(payload.get("value", "1"))
        raise ValueError(f"Action BottomFloor inconnue : {action}")
    def shutdown(self): self.scanner.stop()

def create_module(context): return GarageBackend(context)