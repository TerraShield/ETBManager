"""Moteur du prédicteur de clignotements du garage, indépendant de l'interface."""
from __future__ import annotations

import math
import os
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Optional

try:
    import numpy as np
    import mss
except ImportError:
    np = None
    mss = None

try:
    from pynput import keyboard
except ImportError:
    keyboard = None


RANGES = {
    "1": (4.25, 7.26),
    "2": (4.50, 7.50),
    "3": (5.00, 7.50),
    "4": (5.00, 7.50),
    "Inconnu": (4.00, 8.00),
}


def estimate_period(times, player_count):
    if len(times) < 3:
        return None, None
    low, high = RANGES[player_count]
    intervals = [b - a for a, b in zip(times, times[1:]) if b > a]
    if len(intervals) < 2:
        return None, None
    best_t, best_score, best_max = None, float("inf"), float("inf")
    for i in range(int(round((high - low) * 100)) + 1):
        candidate = low + i / 100.0
        residuals = []
        for interval in intervals:
            multiple = max(1, round(interval / candidate))
            residuals.append(abs(interval - multiple * candidate))
        rms = math.sqrt(sum(x * x for x in residuals) / len(residuals))
        max_res = max(residuals)
        if (rms, max_res) < (best_score, best_max):
            best_t, best_score, best_max = candidate, rms, max_res
    if best_score > 0.30 or best_max > 0.60:
        return None, best_score
    return best_t, best_score


class GarageModule:
    def __init__(self, port: int):
        self.port = int(port)
        self.lock = threading.RLock()
        self.region = None
        self.events = []
        self.period = None
        self.last_flicker = None
        self.blackout_started = None
        self.relight_time = None
        self.running = False
        self.samples = deque(maxlen=80)
        self.drops = deque()
        self.last_sample = 0.0
        self.last_detected = 0.0
        self.dark_since = None
        self.baseline = None
        self.current_luma = None
        self.status = "Sélectionne une zone autour d'une lampe pour commencer."
        self.quality_score = None
        self.player_count = "1"
        self.stop_event = threading.Event()
        self.capture_thread: Optional[threading.Thread] = None
        self.listener = None
        self.selector_process = None
        self._start_hotkeys()

    def _start_hotkeys(self):
        if keyboard is None:
            return
        try:
            self.listener = keyboard.GlobalHotKeys({
                "<f8>": self.manual_flicker,
                "<f9>": self.manual_relight,
            })
            self.listener.start()
        except Exception:
            self.listener = None

    def choose_region(self):
        with self.lock:
            if self.selector_process and self.selector_process.poll() is None:
                return {"ok": True, "message": "Le sélecteur de zone est déjà ouvert."}
        script = os.path.join(os.path.dirname(__file__), "region_selector.py")
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen([sys.executable, script, "--port", str(self.port)], **kwargs)
            with self.lock:
                self.selector_process = proc
                self.status = "Sélectionne une zone autour d'une lampe dans la fenêtre qui vient de s'ouvrir."
            return {"ok": True, "message": self.status}
        except Exception as exc:
            with self.lock:
                self.status = f"Impossible d'ouvrir le sélecteur : {exc}"
            return {"ok": False, "message": self.status}

    def set_region(self, region):
        required = ("left", "top", "width", "height")
        if not isinstance(region, dict) or any(k not in region for k in required):
            raise ValueError("Coordonnées de zone incomplètes.")
        clean = {k: int(region[k]) for k in required}
        if clean["width"] < 12 or clean["height"] < 12:
            raise ValueError("La zone sélectionnée est trop petite.")
        with self.lock:
            self.region = clean
            self.samples.clear()
            self.baseline = None
            self.dark_since = None
            self.status = f"Zone sélectionnée : {clean['width']} × {clean['height']} px. Démarre la détection."

    def start(self):
        if mss is None or np is None:
            with self.lock:
                self.status = "Dépendances manquantes : installe les paquets avec pip install -r requirements.txt."
            return False, self.status
        with self.lock:
            if not self.region:
                self.status = "Sélectionne d'abord une zone autour d'une lampe."
                return False, self.status
            if self.running:
                return True, "La détection est déjà active."
            if self.capture_thread and self.capture_thread.is_alive():
                self.status = "Arrêt de la capture en cours, réessaie dans un instant."
                return False, self.status
            self.stop_event.clear()
            self.running = True
            self.status = "Analyse en cours. Attends que le jeu soit dans son état éclairé normal."
            self.last_sample = 0.0
            self.capture_thread = threading.Thread(target=self._capture_loop, name="GarageCapture", daemon=True)
            self.capture_thread.start()
            return True, self.status

    def stop(self):
        with self.lock:
            was_running = self.running
            self.running = False
            self.stop_event.set()
            thread = self.capture_thread
            if was_running:
                self.status = "Détection arrêtée."
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self.lock:
            if thread and not thread.is_alive() and self.capture_thread is thread:
                self.capture_thread = None
            return True, self.status

    def _capture_loop(self):
        try:
            with mss.mss() as capture:
                while not self.stop_event.is_set():
                    with self.lock:
                        region = dict(self.region) if self.region else None
                    if not region:
                        self.stop_event.wait(0.1)
                        continue
                    now = time.perf_counter()
                    try:
                        shot = capture.grab(region)
                        arr = np.asarray(shot, dtype=np.uint8)
                        rgb = arr[:, :, :3].astype(np.float32)
                        luma = float(np.mean(0.2126 * rgb[:, :, 2] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 0]))
                        self.process_luma(now, luma)
                    except Exception as exc:
                        with self.lock:
                            self.status = f"Erreur de capture : {exc}"
                            self.running = False
                        break
                    elapsed = time.perf_counter() - now
                    self.stop_event.wait(max(0.0, 1.0 / 15.0 - elapsed))
        except Exception as exc:
            with self.lock:
                self.status = f"Impossible de capturer l'écran : {exc}"
                self.running = False
        finally:
            with self.lock:
                self.running = False

    def process_luma(self, now, luma):
        with self.lock:
            self.current_luma = luma
            self.samples.append((now, luma))
            if self.baseline is None:
                self.baseline = luma
                return
            if self.dark_since is None:
                self.baseline = 0.985 * self.baseline + 0.015 * luma
            threshold = max(12.0, self.baseline * 0.22)
            is_dark = luma < self.baseline - threshold
            if is_dark:
                if self.dark_since is None:
                    self.dark_since = now
                    self.drops.append(now)
                if now - self.dark_since > 1.25 and self.blackout_started is None:
                    self.blackout_started = self.dark_since
                    self.relight_time = None
                    self.status = "Extinction prolongée détectée : attente du rallumage."
            else:
                if self.dark_since is not None:
                    dark_duration = now - self.dark_since
                    if self.blackout_started is not None:
                        if dark_duration > 1.25:
                            self.relight_time = now
                            self.status = "Rallumage détecté automatiquement."
                        self.blackout_started = None
                    self.dark_since = None
            while self.drops and now - self.drops[0] > 0.85:
                self.drops.popleft()
            if len(self.drops) >= 2 and now - self.last_detected > 1.0 and self.blackout_started is None:
                if luma > self.baseline - max(8.0, self.baseline * 0.12):
                    event_time = self.drops[0]
                    if not self.events or event_time - self.events[-1] > 1.0:
                        self.events.append(event_time)
                        self.last_flicker = event_time
                        self.last_detected = now
                        self.drops.clear()
                        self._recalculate_locked()
                        self.status = "Clignotement détecté automatiquement."

    def _recalculate_locked(self):
        self.period, self.quality_score = estimate_period(self.events, self.player_count)

    def recalculate(self):
        with self.lock:
            self._recalculate_locked()

    def set_players(self, value):
        if value not in RANGES:
            raise ValueError("Nombre de joueurs invalide.")
        with self.lock:
            self.player_count = value
            self._recalculate_locked()

    def manual_flicker(self):
        now = time.perf_counter()
        with self.lock:
            if not self.events or now - self.events[-1] > 0.8:
                self.events.append(now)
                self.last_flicker = now
                self._recalculate_locked()
                self.status = "Clignotement ajouté manuellement (F8)."

    def manual_relight(self):
        with self.lock:
            self.relight_time = time.perf_counter()
            self.blackout_started = None
            self.dark_since = None
            self.status = "Rallumage ajouté manuellement (F9)."

    def next_tick(self):
        if self.period is None or self.last_flicker is None or self.relight_time is None:
            return None
        elapsed = self.relight_time - self.last_flicker
        steps = math.floor(elapsed / self.period) + 1
        return self.last_flicker + steps * self.period

    def reset(self):
        with self.lock:
            self.events.clear()
            self.period = None
            self.last_flicker = None
            self.blackout_started = None
            self.relight_time = None
            self.dark_since = None
            self.drops.clear()
            self.baseline = None
            self.samples.clear()
            self.quality_score = None
            self.status = "Calibration réinitialisée."

    def snapshot(self):
        with self.lock:
            target = self.next_tick()
            remaining = None if target is None else max(0.0, target - time.perf_counter())
            if self.period is None:
                period_label = "Calibration en cours" if len(self.events) else "En attente de détection"
                quality = "Collecte de plusieurs clignotements" if len(self.events) < 3 else "Mesures ambiguës, vérifie la zone"
            else:
                period_label = f"{self.period:.2f} s"
                score = self.quality_score or 0.0
                q = "élevée" if score < 0.07 else ("moyenne" if score < 0.16 else "faible")
                quality = f"{q} (écart {score:.3f} s)"
            return {
                "status": self.status,
                "region": self.region,
                "running": self.running,
                "player_count": self.player_count,
                "luma": self.current_luma,
                "event_count": len(self.events),
                "period": self.period,
                "period_label": period_label,
                "quality": quality,
                "remaining": remaining,
                "hotkeys_available": self.listener is not None,
            }

    def shutdown(self):
        self.stop()
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                pass
        if self.selector_process and self.selector_process.poll() is None:
            try:
                self.selector_process.terminate()
            except Exception:
                pass
