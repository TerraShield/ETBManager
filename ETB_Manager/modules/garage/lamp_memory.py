"""Prédicteur BottomFloor : intensité des lampes lue en mémoire -> cycles -> prédiction.

Deux parties indépendantes :

* ``LampCycleTracker`` : logique pure (états des lampes -> épisodes de clignotement ->
  estimation du délai). Aucun accès mémoire, donc testable sans le jeu
  (voir ``tests/test_lamp_cycle.py``).
* ``LampMemoryScanner`` : accès mémoire. Un thread découvre les lampes (lent, mis en cache),
  un autre les échantillonne en continu (rapide) pour que la découverte ne crée jamais de
  trous de mesure.

Modèle du cycle : lumières allumées pendant un délai D -> épisode (impulsions de
clignotement, éventuellement suivies d'une coupure longue) -> rallumage -> délai D suivant.
On mesure D entre le dernier rallumage d'un épisode et le début de l'épisode suivant.
"""
from __future__ import annotations

import logging
import math
import statistics
import struct
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Optional

from memory_reader import (
    PROCESS_NAME, ProcessMemory, find_process_id, find_module, locate_gworld, locate_gnames,
    FNameResolver, _read_unreal_tarray_pointers, _find_unreal_property_offset, _read_i32,
    _read_fname_at, OFF_WORLD_PERSISTENT_LEVEL, OFF_WORLD_LEVELS, OFF_LEVEL_ACTORS,
    OFF_UOBJECT_CLASS, OFF_USTRUCT_SUPER, OFF_USTRUCT_CHILD_PROPERTIES, OFF_FFIELD_NEXT,
    OFF_FFIELD_NAME, OFF_FPROPERTY_OFFSET,
)

log = logging.getLogger("ETB.BottomFloor.Lamps")

MAX_LEVELS, MAX_ACTORS = 256, 250000
POLL_INTERVAL = 0.01        # cadence visée de l'échantillonnage (l'horloge Windows donne ~64-100 Hz)
DISCOVERY_INTERVAL = 1.0    # redécouverte des acteurs (thread séparé, résultats mis en cache)
LIGHT_ON_THRESHOLD = 50.0   # même seuil que BP_Lamp.CheckLight(Value)
GLOBAL_ON_RATIO = 0.60      # part de lampes allumées => état collectif « allumé »
GLOBAL_OFF_RATIO = 0.40     # part de lampes allumées => état collectif « éteint » (hystérésis entre les deux)
CONFIRM_SAMPLES = 2         # échantillons consécutifs pour valider un changement d'état
CONFIRM_TIME = 0.02         # ... et durée minimale (une impulsion de 30 ms doit rester visible)
FLICKER_MAX_OFF = 0.70      # une coupure plus longue est une extinction, pas une impulsion
EPISODE_GAP = 1.00          # allumé sans interruption depuis ce délai => l'épisode est terminé
MAX_SAMPLE_GAP = 0.35       # trou de mesure au-delà duquel les durées ne sont plus fiables
RANGE_TOLERANCE = 0.25      # marge autour de RANGES pour absorber l'imprécision de mesure
FIXED_SPREAD_MAX = 0.20     # écart-type sous lequel les délais sont jugés constants
FIXED_SPREAD_MAX_2 = 0.08   # idem quand on n'a que 2 mesures (plus strict)
RECENT_SAMPLES = 12         # nombre de mesures récentes utilisées pour l'estimation
OVERDUE_MARGIN = 0.60       # retard toléré avant de déclarer l'événement manqué
RANGES = {"1": (4.25, 7.26), "2": (4.50, 7.50), "3": (5.00, 7.50), "4": (5.00, 7.50), "Inconnu": (4.00, 8.00)}


@dataclass
class Episode:
    """Une séquence de clignotement / extinction, du premier OFF au dernier rallumage."""
    start: float
    stable_on: Optional[float]      # durée allumée avant le début (None si l'ancrage est inconnu)
    end: Optional[float] = None     # dernier rallumage
    pulses: int = 0                 # nombre de coupures
    max_off: float = 0.0            # plus longue coupure
    blackout_at: Optional[float] = None  # début de la première coupure > FLICKER_MAX_OFF


class LampCycleTracker:
    """Transforme des échantillons (temps, intensités) en cycles et en prédiction."""

    def __init__(self, player_count: str = "1"):
        self.player_count = player_count
        self.reset("init")

    # ------------------------------------------------------------------ état
    def reset(self, reason: str = "reset") -> None:
        self.lamp_on: dict = {}
        self.ever_on: set = set()
        self.toggles: Counter = Counter()
        self.on_ratio: Optional[float] = None
        self.global_state: Optional[bool] = None
        self.candidate: Optional[bool] = None
        self.candidate_since: Optional[float] = None
        self.candidate_count = 0
        self.off_since: Optional[float] = None
        self.episode: Optional[Episode] = None
        self.anchor_start: Optional[float] = None
        self.anchor_end: Optional[float] = None     # dernier rallumage connu : origine du décompte
        self.events: deque = deque(maxlen=80)        # début de chaque épisode terminé
        self.raw_durations: deque = deque(maxlen=60)  # durées allumées mesurées (valides ou non)
        self.leads: deque = deque(maxlen=20)         # début d'épisode -> début de la coupure longue
        self.last_t: Optional[float] = None
        self.last_episode: Optional[dict] = None
        self._last_estimate_key = None
        log.info("TRACKER_RESET reason=%s player_count=%s", reason, self.player_count)

    def set_players(self, value) -> None:
        value = str(value)
        if value not in RANGES:
            raise ValueError("Nombre de joueurs invalide.")
        previous, self.player_count = self.player_count, value
        log.info("PREDICTION_CONTEXT_CHANGED player_count=%s previous=%s", value, previous)
        self._log_calibration()

    def discontinuity(self, t: float, reason: str) -> None:
        """Mesure interrompue : les durées à cheval sur le trou ne sont plus fiables."""
        log.warning("MEASUREMENT_DISCONTINUITY t=%.3f reason=%s", t, reason)
        self.candidate = None
        self.candidate_count = 0
        self.global_state = None        # l'état au retour de la mesure redevient « initial »
        self.off_since = None
        self.episode = None
        self.anchor_start = self.anchor_end = None

    # -------------------------------------------------------------- mesures
    def valid_durations(self) -> list:
        low, high = RANGES[self.player_count]
        return [d for d in self.raw_durations if low - RANGE_TOLERANCE <= d <= high + RANGE_TOLERANCE]

    @property
    def rejected(self) -> list:
        low, high = RANGES[self.player_count]
        return [d for d in self.raw_durations if not low - RANGE_TOLERANCE <= d <= high + RANGE_TOLERANCE]

    def feed(self, t: float, intensities: dict) -> None:
        """Un échantillon : ``intensities`` = {identifiant de lampe: intensité lue}."""
        if self.last_t is not None and t - self.last_t > MAX_SAMPLE_GAP:
            self.discontinuity(t, "gap=%.2fs" % (t - self.last_t))
        self.last_t = t
        for key in [k for k in self.lamp_on if k not in intensities]:
            del self.lamp_on[key]        # lampe disparue (streaming de niveau) : on ne la compte plus
        for key, value in intensities.items():
            on = value > LIGHT_ON_THRESHOLD
            previous = self.lamp_on.get(key)
            if previous is not None and previous != on:
                self.toggles[key] += 1
            self.lamp_on[key] = on
            if on:
                self.ever_on.add(key)
        # Ratio calculé sur les lampes qui ont réellement basculé (les lampes décoratives
        # toujours allumées ou toujours éteintes ne diluent plus le ratio). Avant le premier
        # basculement, on prend toutes les lampes déjà vues allumées.
        pool = [k for k in intensities if self.toggles[k] > 0] or [k for k in intensities if k in self.ever_on]
        self.on_ratio = (sum(self.lamp_on[k] for k in pool) / len(pool)) if pool else 0.0
        raw = True if self.on_ratio >= GLOBAL_ON_RATIO else False if self.on_ratio <= GLOBAL_OFF_RATIO else None
        self._advance(t, raw)
        ep = self.episode
        if ep is not None and ep.end is not None and self.global_state is True and t - ep.end >= EPISODE_GAP:
            self._close_episode(ep)

    def _advance(self, t: float, raw: Optional[bool]) -> None:
        if raw is None or raw == self.global_state:
            self.candidate, self.candidate_count = None, 0
            return
        if raw != self.candidate:
            self.candidate, self.candidate_since, self.candidate_count = raw, t, 1
        else:
            self.candidate_count += 1
        if self.candidate_count >= CONFIRM_SAMPLES and t - self.candidate_since >= CONFIRM_TIME:
            edge_time = self.candidate_since    # instant du premier échantillon dans le nouvel état
            self.candidate, self.candidate_count = None, 0
            self._edge(edge_time, raw)

    def _edge(self, t: float, state: bool) -> None:
        if self.global_state is None:
            # L'état initial n'est jamais un événement et ne fournit aucun ancrage.
            self.global_state = state
            self.off_since = None if state else t
            log.info("LAMP_INITIAL_STATE state=%s on_ratio=%.3f lamps=%d player_count=%s",
                     "ON" if state else "OFF", self.on_ratio or 0.0, len(self.lamp_on), self.player_count)
            return
        self.global_state = state
        log.info("LAMP_STATE_TRANSITION to=%s on_ratio=%.3f lamps=%d timestamp=%.3f",
                 "ON" if state else "OFF", self.on_ratio or 0.0, len(self.lamp_on), t)
        if not state:                                   # ON -> OFF
            if self.episode is None:
                stable = (t - self.anchor_end) if self.anchor_end is not None else None
                self.episode = Episode(start=t, stable_on=stable)
            self.episode.pulses += 1
            self.off_since = t
            return
        off = (t - self.off_since) if self.off_since is not None else None   # OFF -> ON
        ep = self.episode
        if ep is None:
            # Démarrage pendant une coupure : ce rallumage sert d'ancrage, ce n'est pas un événement.
            self.anchor_end, self.anchor_start = t, None
        else:
            ep.end = t
            if off is not None:
                ep.max_off = max(ep.max_off, off)
                if off > FLICKER_MAX_OFF and ep.blackout_at is None:
                    ep.blackout_at = self.off_since
        self.off_since = None

    def _close_episode(self, ep: Episode) -> None:
        self.episode = None
        self.events.append(ep.start)
        self.anchor_start, self.anchor_end = ep.start, ep.end
        if ep.blackout_at is not None:
            self.leads.append(ep.blackout_at - ep.start)
        if ep.stable_on is not None:
            self.raw_durations.append(ep.stable_on)
        self.last_episode = {"start": ep.start, "end": ep.end, "pulses": ep.pulses,
                             "max_off": ep.max_off, "blackout": ep.blackout_at is not None,
                             "stable_on": ep.stable_on}
        low, high = RANGES[self.player_count]
        in_range = ep.stable_on is not None and low - RANGE_TOLERANCE <= ep.stable_on <= high + RANGE_TOLERANCE
        log.info("FLICKER_SEQUENCE_CONFIRMED start=%.3f end=%.3f pulses=%d max_off=%.3f blackout=%s "
                 "preceding_on_duration=%s in_range=%s cycles=%d player_count=%s",
                 ep.start, ep.end, ep.pulses, ep.max_off, ep.blackout_at is not None,
                 round(ep.stable_on, 3) if ep.stable_on is not None else None, in_range,
                 len(self.events), self.player_count)
        self._log_calibration()

    def _log_calibration(self) -> None:
        est = self.estimate()
        key = (est["mode"], round(est.get("period") or 0, 2), round(est.get("lo") or 0, 2),
               round(est.get("hi") or 0, 2), est["n"], len(self.rejected))
        if key != self._last_estimate_key:
            self._last_estimate_key = key
            log.info("CALIBRATION_UPDATED player_count=%s mode=%s samples=%d rejected=%d durations=%s estimate=%s",
                     self.player_count, est["mode"], est["n"], len(self.rejected),
                     [round(v, 3) for v in self.raw_durations], {k: v for k, v in est.items() if k != "n"})

    # ------------------------------------------------------------ estimation
    def estimate(self) -> dict:
        """Délai allumé -> prochain épisode.

        * ``fixed``  : les mesures sont quasi identiques -> décompte précis.
        * ``window`` : les mesures varient (délai aléatoire à chaque cycle) -> fenêtre [lo, hi].
        """
        low, high = RANGES[self.player_count]
        valid = self.valid_durations()[-RECENT_SAMPLES:]
        n = len(valid)
        spread = statistics.pstdev(valid) if n >= 2 else None
        if n >= 2 and spread <= (FIXED_SPREAD_MAX if n >= 3 else FIXED_SPREAD_MAX_2):
            return {"mode": "fixed", "period": statistics.median(valid), "spread": spread, "n": n}
        # Délai tiré au hasard à chaque cycle : seules les bornes du jeu (RANGES) sont garanties.
        # On ne resserre pas la fenêtre sur les extrêmes observés : le prochain tirage a une bonne
        # probabilité de les dépasser (n mesures uniformes => couverture de seulement (n-1)/(n+1)).
        return {"mode": "window", "lo": low, "hi": high, "spread": spread, "n": n}

    def snapshot(self, now: float) -> dict:
        est = self.estimate()
        mode, n = est["mode"], est["n"]
        state_key = "on" if self.global_state is True else "off" if self.global_state is False else "idle"
        if self.global_state is False:
            timer_state = "off"
        elif self.episode is not None:
            timer_state = "pending"
        elif self.anchor_end is not None and self.global_state is True:
            timer_state = "running"
        else:
            timer_state = "calibrating"

        remaining = remaining_min = remaining_max = None
        overdue = False
        if timer_state == "running":
            elapsed = now - self.anchor_end
            if mode == "fixed":
                margin = max(0.12, 2.0 * (est["spread"] or 0.0))
                central = est["period"] - elapsed
                remaining = max(0.0, central)
                remaining_min, remaining_max = max(0.0, central - margin), max(0.0, central + margin)
                overdue = central < -OVERDUE_MARGIN
            else:
                lo, hi = est["lo"], est["hi"]
                remaining_min, remaining_max = max(0.0, lo - elapsed), max(0.0, hi - elapsed)
                remaining = max(0.0, (max(lo, elapsed) + hi) / 2.0 - elapsed)    # espérance si tirage uniforme
                overdue = elapsed > hi + OVERDUE_MARGIN

        rejected = self.rejected
        if mode == "fixed":
            period_label = "%.2f s" % est["period"]
            spread = est["spread"] or 0.0
            quality = ("élevée" if spread < 0.07 else "moyenne" if spread < 0.16 else "faible") + " (écart %.3f s)" % spread
        else:
            period_label = "%.2f – %.2f s" % (est["lo"], est["hi"]) + (" (aléatoire)" if n >= 3 else "")
            if n >= 3:
                quality = "délai variable (écart %.2f s) : fenêtre seulement" % (est["spread"] or 0.0)
            else:
                quality = "calibration : %d/2 mesure(s) valide(s)" % n

        if timer_state == "calibrating":
            progress = "En attente du premier clignotement" if not self.events else "En attente du rallumage"
        else:
            progress = None
        lead = statistics.median(self.leads) if len(self.leads) >= 2 else None
        return {
            "lamp_state": "Allumées" if state_key == "on" else "Éteintes" if state_key == "off" else "Recherche…",
            "lamp_state_key": state_key, "timer_state": timer_state,
            "remaining": remaining, "remaining_min": remaining_min, "remaining_max": remaining_max,
            "overdue": overdue, "prediction_mode": mode, "period": est.get("period"),
            "window": [est["lo"], est["hi"]] if mode == "window" else None,
            "period_label": period_label, "quality": quality, "progress_label": progress,
            "calibration_samples": n, "event_count": len(self.events),
            "rejected_count": len(rejected), "last_rejected": rejected[-1] if rejected else None,
            "blackout_lead": lead, "on_ratio": self.on_ratio,
            "dynamic_lamps": sum(1 for k in self.lamp_on if self.toggles[k] > 0),
            "last_episode": dict(self.last_episode) if self.last_episode else None,
            "blackout": state_key == "off",
        }


class LampMemoryScanner:
    """Accès mémoire : découverte des BP_Lamp (thread 1) et échantillonnage rapide (thread 2)."""

    def __init__(self):
        log.info("SCANNER_INIT_BEGIN poll_interval=%.3fs discovery_interval=%.3fs process_name=%s",
                 POLL_INTERVAL, DISCOVERY_INTERVAL, PROCESS_NAME)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.status = "Connexion au jeu et recherche des lampadaires…"
        self.world_name: Optional[str] = None
        self.in_bottomfloor = False
        self.last_scan: Optional[float] = None
        self.lamps: dict = {}           # actor -> infos d'affichage
        self.cache: list = []           # lampes lisibles : actor, component, offset
        self.stats = {"actors_seen": 0, "lamp_actors": 0, "readable": 0, "unreadable": 0}
        self.tracker = LampCycleTracker("1")
        self.signal: Optional[float] = None
        self.last_sample_at: Optional[float] = None
        self.sample_hz_actual = 0.0
        self._last_error_log = 0.0
        log.info("SCANNER_INIT_OK")

    # ----------------------------------------------------------- commandes
    def start(self):
        with self.lock:
            if self.running:
                return {"ok": True, "message": "Prédicteur déjà actif."}
            self.stop_event = threading.Event()      # un événement par session : pas de chevauchement au redémarrage
            self.running = True
            self.status = "Connexion au jeu et recherche des lampadaires…"
            self.thread = threading.Thread(target=self._run, args=(self.stop_event,), name="BottomFloorLampPredictor", daemon=True)
            self.thread.start()
        log.info("Démarrage du prédicteur mémoire (cadence visée %.0f Hz).", 1 / POLL_INTERVAL)
        return {"ok": True, "message": self.status}

    def stop(self):
        self.stop_event.set()
        with self.lock:
            self.running = False
            self.status = "Prédicteur arrêté."
        log.info("Arrêt du prédicteur BottomFloor.")
        return {"ok": True, "message": self.status}

    def clear(self):
        with self.lock:
            self.tracker.reset("manual_clear")
            self.status = "Calibration effacée. Les mesures mémoire continuent."
        return {"ok": True}

    def set_players(self, value):
        with self.lock:
            self.tracker.set_players(value)
        return {"ok": True}

    # ------------------------------------------------------------- threads
    def _run(self, stop: threading.Event):
        mem = None
        discovery = None
        try:
            log.info("MEMORY_CONNECT_BEGIN process_name=%s", PROCESS_NAME)
            pid = find_process_id(PROCESS_NAME)
            mem = ProcessMemory(pid)
            base, module_size = find_module(pid, PROCESS_NAME)
            slot = locate_gworld(mem, base)
            names = locate_gnames(mem, base)
            resolver = FNameResolver(mem, names)
            log.info("Connexion PID=%s base=0x%X GWorldSlot=0x%X GNames=0x%X", pid, base, slot, names)
            discovery = threading.Thread(target=self._discovery_loop, args=(mem, slot, resolver, stop),
                                         name="BottomFloorLampDiscovery", daemon=True)
            discovery.start()
            while not stop.is_set():
                try:
                    self._sample_cached(mem)
                except Exception:
                    self._rate_limited_exception("Erreur pendant l'échantillonnage rapide")
                stop.wait(POLL_INTERVAL)
        except Exception as exc:
            with self.lock:
                self.status = "Connexion impossible : " + str(exc)
            log.exception("SCANNER_FATAL_ERROR exception=%s message=%s", type(exc).__name__, exc)
        finally:
            stop.set()
            if discovery is not None:
                discovery.join(timeout=3.0)
            if mem:
                mem.close()
            with self.lock:
                if self.stop_event is stop:
                    self.running = False
            log.info("SCANNER_THREAD_EXIT")

    def _rate_limited_exception(self, message: str):
        now = time.monotonic()
        if now - self._last_error_log > 5.0:
            self._last_error_log = now
            log.exception(message)

    def _discovery_loop(self, mem, slot, resolver, stop: threading.Event):
        ctx = {"known": {}, "layouts": {}, "comp_ints": {}, "world": None}
        last_summary = None
        while not stop.is_set():
            try:
                world = mem.ptr(slot)
                if not world:
                    raise RuntimeError("GWorld nul")
                world_name = resolver.object_name(world)
                with self.lock:
                    previous = self.world_name
                if world_name != ctx["world"]:
                    ctx.update(world=world_name, known={})       # nouveau monde : on reclasse les acteurs
                    log.info("WORLD_CHANGED from=%s to=%s", previous, world_name)
                result = self._scan_world(mem, world, resolver, ctx)
                in_bf = world_name.casefold() == "bottomfloor"
                with self.lock:
                    if world_name != previous:
                        self.tracker.reset("world_changed")      # les lampes d'un autre niveau ne doivent pas polluer la calibration
                    self.cache, self.lamps, self.stats = result["cache"], result["lamps"], result["stats"]
                    self.world_name, self.in_bottomfloor, self.last_scan = world_name, in_bf, time.time()
                    self.status = result["status"] if in_bf else "Niveau courant : %s (BottomFloor attendu)." % world_name
                summary = (world_name, result["stats"]["actors_seen"] // 50, result["stats"]["lamp_actors"],
                           result["stats"]["readable"], len(result["cache"]))
                if summary != last_summary:
                    last_summary = summary
                    log.info("LAMP_DISCOVERY_OK world=%s actors=%s lamps=%s readable=%s cached=%s", world_name,
                             result["stats"]["actors_seen"], result["stats"]["lamp_actors"],
                             result["stats"]["readable"], len(result["cache"]))
            except Exception as exc:
                with self.lock:
                    self.status = "Erreur de découverte : " + str(exc)
                self._rate_limited_exception("Échec de découverte des lampadaires")
            stop.wait(DISCOVERY_INTERVAL)

    # ---------------------------------------------------------- découverte
    def _describe_class_properties(self, mem, resolver, class_address):
        classes = []
        current = class_address
        visited = set()
        while current and current not in visited and len(classes) < 32:
            visited.add(current)
            class_name = resolver.object_name(current)
            fields = []
            field = mem.ptr(current + OFF_USTRUCT_CHILD_PROPERTIES)
            seen = set()
            steps = 0
            while field and field not in seen and steps < 4096:
                seen.add(field)
                steps += 1
                fields.append({"name": _read_fname_at(mem, resolver, field + OFF_FFIELD_NAME),
                               "offset": _read_i32(mem, field + OFF_FPROPERTY_OFFSET)})
                field = mem.ptr(field + OFF_FFIELD_NEXT)
            classes.append({"name": class_name, "fields": fields, "field_count": steps})
            current = mem.ptr(current + OFF_USTRUCT_SUPER)
        return classes

    def _class_layout(self, mem, resolver, class_address, layouts):
        """Disposition d'une classe de lampe : identique pour toutes les instances, donc calculée une fois."""
        layout = layouts.get(class_address)
        if layout is None:
            hierarchy = self._describe_class_properties(mem, resolver, class_address)
            props = [p for c in hierarchy for p in c["fields"]]
            layout = {
                "comp_off": next((p["offset"] for p in props if p["name"].casefold() == "spotlight" and p["offset"] is not None), None),
                "hierarchy": " > ".join(c["name"] for c in hierarchy),
            }
            layouts[class_address] = layout
            log.info("LAMP_CLASS class=%s hierarchy=%s spotlight_offset=%s", resolver.object_name(class_address),
                     layout["hierarchy"], layout["comp_off"])
        return layout

    def _scan_world(self, mem, world, resolver, ctx):
        known, layouts, comp_ints = ctx["known"], ctx["layouts"], ctx["comp_ints"]
        levels = []
        persistent = mem.ptr(world + OFF_WORLD_PERSISTENT_LEVEL)
        if persistent:
            levels.append(persistent)
        levels.extend(_read_unreal_tarray_pointers(mem, world + OFF_WORLD_LEVELS, MAX_LEVELS))
        seen, lamps, cache = set(), {}, []
        for level in dict.fromkeys(levels):
            for actor in _read_unreal_tarray_pointers(mem, level + OFF_LEVEL_ACTORS, MAX_ACTORS):
                if actor in seen:
                    continue
                seen.add(actor)
                entry = known.get(actor)
                if entry is not None and entry["lamp"] and mem.ptr(actor + OFF_UOBJECT_CLASS) != entry["cls"]:
                    known.pop(actor, None)          # adresse réutilisée par un autre objet
                    entry = None
                if entry is None:
                    cls = mem.ptr(actor + OFF_UOBJECT_CLASS)
                    if not cls:
                        continue
                    class_name, actor_name = resolver.object_name(cls), resolver.object_name(actor)
                    is_lamp = (class_name.casefold() in ("bp_lamp_c", "bp_lamp")
                               or actor_name.casefold().startswith(("sm_mp_lamp_", "bp_lamp")))
                    entry = known[actor] = {"cls": cls, "class_name": class_name, "name": actor_name, "lamp": is_lamp}
                if not entry["lamp"]:
                    continue
                item, readable = self._read_lamp(mem, resolver, actor, entry, layouts, comp_ints)
                lamps[actor] = item
                if readable is not None:
                    cache.append(readable)
        for stale in [a for a in known if a not in seen]:
            del known[stale]
        readable_count = sum(x["intensity"] is not None for x in lamps.values())
        if lamps:
            status = "%d lampadaire(s), %d intensité(s) lisible(s). Échantillonnage visé : %.0f Hz." % (
                len(lamps), readable_count, 1 / POLL_INTERVAL)
        else:
            status = "Aucun lampadaire trouvé dans les niveaux chargés."
        return {"lamps": lamps, "cache": cache,
                "stats": {"actors_seen": len(seen), "lamp_actors": len(lamps), "readable": readable_count,
                          "unreadable": len(lamps) - readable_count},
                "status": status}

    def _read_lamp(self, mem, resolver, actor, entry, layouts, comp_ints):
        layout = self._class_layout(mem, resolver, entry["cls"], layouts)
        comp_off = layout["comp_off"]
        component = mem.ptr(actor + comp_off) if comp_off is not None else 0
        intensity, int_off = None, None
        status = "Spotlight absent" if comp_off is None else "Pointeur Spotlight illisible"
        if component:
            comp_cls = mem.ptr(component + OFF_UOBJECT_CLASS)
            if comp_cls:
                int_off = comp_ints.get(comp_cls)
                if int_off is None:
                    int_off = _find_unreal_property_offset(mem, resolver, comp_cls, "Intensity")
                    if int_off is not None:
                        comp_ints[comp_cls] = int_off
            if int_off is None:
                status = "FProperty Intensity introuvable"
            else:
                raw = mem.read(component + int_off, 4)
                if not raw:
                    status = "Intensité illisible"
                else:
                    value = struct.unpack("<f", raw)[0]
                    if math.isfinite(value):
                        intensity, status = round(value, 5), "OK"
                    else:
                        status = "Intensité non finie"
        item = {"name": entry["name"], "class": entry["class_name"], "actor": "%X" % actor,
                "intensity": intensity, "read_status": status}
        readable = {"actor": actor, "component": component, "offset": int_off} if component and int_off is not None else None
        return item, readable

    # ------------------------------------------------------- échantillonnage
    def _sample_cached(self, mem):
        """Lit l'intensité de chaque lampe en cache puis nourrit le tracker.

        L'horodatage est pris APRÈS la lecture (jamais avant une opération potentiellement longue),
        sinon toute pause de découverte décalerait les fronts.
        """
        with self.lock:
            cache = self.cache
            feeding = self.in_bottomfloor and self.running
        values = {}
        for item in cache:
            raw = mem.read(item["component"] + item["offset"], 4)
            if not raw:
                continue
            value = struct.unpack("<f", raw)[0]
            if not math.isfinite(value) or not -1.0 <= value <= 100000.0:
                continue
            values[item["actor"]] = max(0.0, value)
        t = time.perf_counter()
        with self.lock:
            if self.last_sample_at is not None and t > self.last_sample_at:
                measured = 1.0 / (t - self.last_sample_at)
                self.sample_hz_actual = measured if self.sample_hz_actual <= 0 else 0.8 * self.sample_hz_actual + 0.2 * measured
            self.last_sample_at = t
            if not values:
                return
            for actor, value in values.items():
                lamp = self.lamps.get(actor)
                if lamp is not None:
                    lamp["intensity"], lamp["read_status"] = round(value, 5), "OK"
            self.signal = sum(min(v, 100.0) for v in values.values()) / len(values)
            if feeding:
                self.tracker.feed(t, values)

    # -------------------------------------------------------------- lecture
    def snapshot(self):
        with self.lock:
            data = self.tracker.snapshot(time.perf_counter())
            if not self.running:
                data.update(lamp_state="Arrêté", lamp_state_key="idle", timer_state="calibrating", remaining=None,
                            remaining_min=None, remaining_max=None)
            elif not self.in_bottomfloor and self.world_name:
                data.update(lamp_state="Hors BottomFloor", lamp_state_key="idle", timer_state="calibrating",
                            remaining=None, remaining_min=None, remaining_max=None,
                            progress_label="Niveau courant : %s" % self.world_name)
            lamps = [{"name": l["name"], "intensity": l["intensity"], "read_status": l["read_status"],
                      "on": None if l["intensity"] is None else l["intensity"] > LIGHT_ON_THRESHOLD}
                     for l in sorted(self.lamps.values(), key=lambda x: x["name"])]
            data.update({
                "running": self.running, "status": self.status, "world_name": self.world_name,
                "last_scan": self.last_scan, "lamps": lamps, "stats": dict(self.stats),
                "sample_hz": round(self.sample_hz_actual, 1) if self.sample_hz_actual > 0 else 1 / POLL_INTERVAL,
                "sample_hz_target": 1 / POLL_INTERVAL, "signal": self.signal,
                "player_count": self.tracker.player_count, "difficulty": "unknown",
            })
            return data
