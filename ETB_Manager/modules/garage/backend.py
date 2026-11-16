"""Adaptateur ETB Manager pour le moteur du prédicteur de garage existant."""
from __future__ import annotations

from .module import GarageModule


class GarageBackend:
    def __init__(self, context):
        self.engine = GarageModule(context.port)

    def snapshot(self, shared_state):
        return self.engine.snapshot()

    def action(self, action, payload, shared_state):
        if action == "choose_region":
            return self.engine.choose_region()
        if action == "start":
            ok, message = self.engine.start()
            return {"ok": ok, "message": message}
        if action == "stop":
            ok, message = self.engine.stop()
            return {"ok": ok, "message": message}
        if action == "reset":
            self.engine.reset()
            return {"ok": True, "message": "Calibration réinitialisée."}
        if action == "flicker":
            self.engine.manual_flicker()
            return {"ok": True}
        if action == "relight":
            self.engine.manual_relight()
            return {"ok": True}
        if action == "players":
            self.engine.set_players(str(payload.get("value", "1")))
            return {"ok": True}
        if action == "set_region":
            self.engine.set_region(payload)
            return {"ok": True, "message": "Zone enregistrée."}
        raise ValueError(f"Action garage inconnue : {action}")

    def shutdown(self):
        self.engine.shutdown()


def create_module(context):
    return GarageBackend(context)
