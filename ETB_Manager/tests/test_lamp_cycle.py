"""Tests du prédicteur BottomFloor, sans le jeu : séries d'intensités synthétiques.

Lancer : python tests/test_lamp_cycle.py
"""
from __future__ import annotations

import json
import logging
import random
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import modules.garage.lamp_memory as lm  # noqa: E402
from modules.garage.lamp_memory import LampCycleTracker  # noqa: E402

logging.disable(logging.CRITICAL)

FLICKER = [(0.12, 0.15), (0.10, 0.0)]       # OFF 0.12 s, ON 0.15 s, OFF 0.10 s, puis rallumage


def build(delays, pulses=FLICKER, blackout=None, start=0.0):
    """Chronologie des coupures : renvoie (intervalles OFF, [(début, dernier rallumage), ...])."""
    off, episodes, t = [], [], start
    for delay in delays:
        s = t + delay
        cur = s
        for i, (o, on) in enumerate(pulses):
            off.append((cur, cur + o))
            cur += o + (on if i < len(pulses) - 1 else 0.0)
        if blackout:
            off.append((cur, cur + blackout))
            cur += blackout
        episodes.append((s, cur))
        t = cur
    return off, episodes


def run(tracker, off, t_end, dt=0.012, lamps=8, static=0, gaps=(), stagger=0.004, seed=1, t0=0.0, checkpoints=None):
    rng = random.Random(seed)
    t, snaps = t0, []
    pending = sorted(checkpoints or [])
    while t < t_end:
        t += dt + rng.uniform(-0.002, 0.002)
        if any(a <= t < b for a, b in gaps):
            continue
        values = {}
        for i in range(lamps):
            tt = t - i * stagger
            values[("dyn", i)] = 0.0 if any(a <= tt < b for a, b in off) else 100.0
        for i in range(static):
            values[("static", i)] = 100.0
        tracker.feed(t, values)
        while pending and t >= pending[0]:
            snaps.append((pending.pop(0), tracker.snapshot(t), t))
    return snaps


def test_fixed_period():
    delays = [6.0] * 7
    off, eps = build(delays, start=1.0)
    tr = LampCycleTracker("1")
    # on regarde la prédiction juste après la clôture de chaque épisode
    checks = [e[1] + 1.2 for e in eps[:-1]]
    snaps = run(tr, off, eps[-1][1] + 2.0, checkpoints=checks)
    s = tr.snapshot(eps[-1][1] + 2.0)
    assert s["event_count"] == len(eps), s["event_count"]
    assert len(tr.raw_durations) == len(eps) - 1, "le premier épisode n'a pas d'ancrage : aucune durée ne doit être inventée"
    assert s["prediction_mode"] == "fixed" and abs(s["period"] - 6.0) < 0.05, s
    for (cp, snap, t), (start, _end) in list(zip(snaps, eps[1:]))[2:]:
        predicted = t + snap["remaining"]
        assert abs(predicted - start) < 0.06, (predicted, start)
    print("OK  période fixe : %d cycles, T=%.3f s, erreur de prédiction < 60 ms" % (s["event_count"], s["period"]))


def test_blackout_episodes_are_calibrated():
    # la dernière impulsion se fond dans la coupure longue : celle-ci commence à +0,27 s
    off, eps = build([5.5] * 6, blackout=2.5, start=0.5)
    tr = LampCycleTracker("1")
    run(tr, off, eps[-1][1] + 2.0)
    s = tr.snapshot(eps[-1][1] + 2.0)
    assert s["event_count"] == len(eps) and s["calibration_samples"] == len(eps) - 1, s
    assert abs(s["period"] - 5.5) < 0.05 and s["prediction_mode"] == "fixed", s
    assert s["blackout_lead"] is not None and abs(s["blackout_lead"] - (0.12 + 0.15)) < 0.06, s["blackout_lead"]
    print("OK  clignotements suivis d'une extinction longue : calibrés (T=%.2f s, coupure complète +%.2f s)" % (s["period"], s["blackout_lead"]))


def test_random_delay_gives_a_window_that_contains_the_truth():
    rng = random.Random(7)
    delays = [rng.uniform(4.25, 7.26) for _ in range(14)]
    off, eps = build(delays, start=0.5)
    tr = LampCycleTracker("1")
    checks = [e[1] + 1.2 for e in eps[:-1]]
    snaps = run(tr, off, eps[-1][1] + 2.0, checkpoints=checks)
    inside = 0
    for (cp, snap, t), (start, _e) in zip(snaps, eps[1:]):
        assert snap["prediction_mode"] in ("window", "fixed")
        truth = start - t
        if snap["remaining_min"] - 0.3 <= truth <= snap["remaining_max"] + 0.3:
            inside += 1
    s = tr.snapshot(eps[-1][1] + 2.0)
    assert s["prediction_mode"] == "window", s["prediction_mode"]
    assert inside == len(snaps), "%d/%d fenêtres contiennent le vrai instant" % (inside, len(snaps))
    print("OK  délai aléatoire : mode fenêtre, %d/%d fenêtres contiennent le vrai instant" % (inside, len(snaps)))


def test_measurement_gap_does_not_invent_durations():
    off, eps = build([6.0] * 6, start=1.0)
    gap = (eps[1][1] + 2.0, eps[1][1] + 2.9)          # trou de 0,9 s entre deux épisodes
    tr = LampCycleTracker("1")
    run(tr, off, eps[-1][1] + 2.0, gaps=[gap])
    assert all(abs(d - 6.0) < 0.08 for d in tr.raw_durations), list(tr.raw_durations)
    assert len(tr.raw_durations) >= 3
    print("OK  trou de mesure : aucune durée faussée (%d mesures saines)" % len(tr.raw_durations))


def test_start_during_blackout_has_no_bogus_anchor():
    off, eps = build([6.0] * 5, blackout=3.0, start=0.0)
    # le suivi démarre au milieu de la première extinction longue
    t0 = eps[0][1] - 1.5
    tr = LampCycleTracker("1")
    run(tr, off, eps[-1][1] + 2.0, t0=t0)
    assert all(abs(d - 6.0) < 0.08 for d in tr.raw_durations), list(tr.raw_durations)
    # l'épisode 0 n'est pas observé (on arrive en pleine coupure) mais son rallumage ancre l'épisode 1 :
    # les épisodes 1 à 4 fournissent chacun une durée vraie
    assert len(tr.raw_durations) == len(eps) - 1, (len(tr.raw_durations), len(eps))
    assert tr.snapshot(0)["event_count"] == len(eps) - 1
    print("OK  démarrage pendant une coupure : ancrage correct, aucune durée inventée")


def test_static_lamps_do_not_hide_the_blackout():
    off, eps = build([6.0] * 6, blackout=2.0, start=0.5)
    tr = LampCycleTracker("1")
    run(tr, off, eps[-1][1] + 2.0, lamps=6, static=8)
    s = tr.snapshot(eps[-1][1] + 2.0)
    assert s["event_count"] >= len(eps) - 1, s["event_count"]
    assert s["dynamic_lamps"] == 6, s["dynamic_lamps"]
    print("OK  lampes décoratives toujours allumées : ignorées après le premier basculement (%d/%d cycles)" % (s["event_count"], len(eps)))


def test_wrong_player_count_is_visible_and_recoverable():
    off, eps = build([4.4] * 6, start=0.5)
    tr = LampCycleTracker("3")                          # plage 5.0-7.5 s : les 4,4 s sont hors plage
    run(tr, off, eps[-1][1] + 2.0)
    s = tr.snapshot(eps[-1][1] + 2.0)
    assert s["rejected_count"] == len(eps) - 1 and s["calibration_samples"] == 0, s
    tr.set_players("1")                                 # plage 4.25-7.26 : le rejet est recalculé rétroactivement
    s = tr.snapshot(eps[-1][1] + 2.0)
    assert s["rejected_count"] == 0 and s["prediction_mode"] == "fixed" and abs(s["period"] - 4.4) < 0.05, s
    print("OK  mauvais nombre de joueurs : mesures hors plage signalées puis récupérées en changeant le réglage")


def test_short_pulses_are_seen():
    pulses = [(0.04, 0.12), (0.04, 0.12), (0.04, 0.0)]
    off, eps = build([5.0] * 5, pulses=pulses, start=0.5)
    tr = LampCycleTracker("1")
    run(tr, off, eps[-1][1] + 2.0, dt=0.010)
    assert tr.snapshot(0)["event_count"] == len(eps), tr.snapshot(0)["event_count"]
    print("OK  impulsions de 40 ms détectées")


def test_scanner_sampling_path_with_fake_memory():
    clock = [100.0]
    lm.time.perf_counter = lambda: clock[0]             # horloge simulée (le module n'utilise que perf_counter ici)
    off, eps = build([6.0] * 5, start=1.0)

    def intensity(i, t):
        return 0.0 if any(a <= t - i * 0.004 < b for a, b in off) else 100.0

    class FakeMem:
        def read(self, address, size):
            comp = address - 0x10
            return struct.pack("<f", intensity(comp, clock[0] - 100.0))

    sc = lm.LampMemoryScanner()
    sc.running, sc.in_bottomfloor, sc.world_name = True, True, "BottomFloor"
    sc.cache = [{"actor": 0x1000 + i, "component": i, "offset": 0x10} for i in range(8)]
    sc.lamps = {0x1000 + i: {"name": "BP_Lamp_C_%d" % i, "class": "BP_Lamp_C", "actor": "%X" % (0x1000 + i),
                             "intensity": None, "read_status": "OK"} for i in range(8)}
    mem = FakeMem()
    end = eps[-1][1] + 2.0
    while clock[0] - 100.0 < end:
        clock[0] += 0.012
        sc._sample_cached(mem)
    snap = sc.snapshot()
    json.dumps(snap)                                    # doit rester sérialisable pour /api/state
    for key in ("lamp_state", "lamp_state_key", "timer_state", "remaining", "period_label", "quality", "event_count", "lamps", "stats"):
        assert key in snap, key
    assert snap["event_count"] == len(eps) and abs(snap["period"] - 6.0) < 0.06, snap
    assert snap["lamps"][0]["on"] is True and "hierarchy" not in snap["lamps"][0]
    sc.in_bottomfloor, sc.world_name = False, "Level0"
    assert sc.snapshot()["lamp_state"] == "Hors BottomFloor"
    print("OK  chaîne complète scanner -> tracker -> snapshot (mémoire simulée), snapshot sérialisable")


def main():
    test_fixed_period()
    test_blackout_episodes_are_calibrated()
    test_random_delay_gives_a_window_that_contains_the_truth()
    test_measurement_gap_does_not_invent_durations()
    test_start_during_blackout_has_no_bogus_anchor()
    test_static_lamps_do_not_hide_the_blackout()
    test_wrong_player_count_is_visible_and_recoverable()
    test_short_pulses_are_seen()
    test_scanner_sampling_path_with_fake_memory()
    print("Tous les tests du prédicteur de lampes passent.")


if __name__ == "__main__":
    main()
