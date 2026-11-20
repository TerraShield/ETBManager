"""Backend du module BottomFloor : diagnostic mémoire des lampadaires."""
from __future__ import annotations
import logging

log = logging.getLogger("ETB.BottomFloor.Backend")


class GarageBackend:
    def __init__(self, context):
        log.info("BACKEND_INIT_BEGIN module_id=%s module_dir=%s base_dir=%s port=%s",
                 context.module_id, context.module_dir, context.base_dir, context.port)
        from .lamp_memory import LampMemoryScanner
        log.info("BACKEND_SCANNER_IMPORT_OK class_module=%s", LampMemoryScanner.__module__)
        self.scanner = LampMemoryScanner()
        self.scanner.start()  # Le suivi mémoire démarre dès le chargement du module.
        log.info("BACKEND_INIT_OK scanner=%s auto_started=True", type(self.scanner).__name__)

    def snapshot(self, shared_state):
        try:
            return self.scanner.snapshot()
        except Exception:
            log.exception("BACKEND_SNAPSHOT_ERROR")
            raise

    def action(self, action, payload, shared_state):
        log.info("BACKEND_ACTION_BEGIN action=%s payload_keys=%s",
                 action, sorted(payload.keys()) if isinstance(payload, dict) else type(payload).__name__)
        try:
            if action == "start":
                result = self.scanner.start()
            elif action == "stop":
                result = self.scanner.stop()
            elif action == "clear":
                result = self.scanner.clear()
            elif action == "players":
                result = self.scanner.set_players(payload.get("value", "1"))
            else:
                raise ValueError(f"Action BottomFloor inconnue : {action}")
            log.info("BACKEND_ACTION_OK action=%s result=%s", action, result)
            return result
        except Exception:
            log.exception("BACKEND_ACTION_FAILED action=%s", action)
            raise

    def shutdown(self):
        log.info("BACKEND_SHUTDOWN")
        self.scanner.stop()


def create_module(context):
    log.info("BACKEND_FACTORY_BEGIN module_id=%s", context.module_id)
    instance = GarageBackend(context)
    log.info("BACKEND_FACTORY_OK instance=%s", type(instance).__name__)
    return instance
