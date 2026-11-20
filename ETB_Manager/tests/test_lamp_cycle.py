"""Tests synthétiques du prédicteur BottomFloor, sans connexion au jeu.

Lancer depuis ETB_Manager :
    python tests/test_lamp_cycle.py
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

FLICKERS = ((-2.2, 0.12), (-1.4, 0.10))


def build(delays, blackout=2.0, flickers=FLICKERS, start=0.5):
    """Construit les coupures OFF et les couples (rallumage, extinction)."""
    off, cycles, relight = [], [], start
    for delay in delays:
        extinction = relight + delay
        for relative, duration in flickers:
            off.append((extinction + relative, extinction + relative + duration))
        off.append((extinction, extinction + blackout))
        cycles.append((relight, extinction))
        relight = extinction + blackout
    return off, cycles


def run_tracker(tracker, off_intervals, end, dt=0.01, lamps=8, static=0,
                gaps=(), stagger=0.004, seed=1, start_at=0.0, checkpoints=()):
    rng = random.Random(seed)
    t = start_at
    pending = sorted(checkpoints)
    snapshots = []
    while t < end:
        t += dt + rng.uniform(-0.001, 0.001)
        if any(a <= t < b for a, b in gaps):
            continue
        values = {}
        for i in range(lamps):
            sample_time = t - i * stagger
            is_off = any(a <= sample_time < b for a, b in off_intervals)
            values[("dynamic", i)] = 0.0 if is_off else 100.0
        for i in range(static):
            values[("static", i)] = 100.0
        tracker.feed(t, values)
        while pending and t >= pending[0]:
            snapshots.append((pending.pop(0), tracker.snapshot(t), t))
    return snapshots


def test_flickers_do_not_restart_countdown_or_calibration():
    off, cycles = build([6.0] * 7)
    tracker = LampCycleTracker("1")
    extinction = cycles[4][1]
    pulse_start = extinction + FLICKERS[0][0]
    pulse_end = pulse_start + FLICKERS[0][1]
    snapshots = run_tracker(
        tracker, off, cycles[-1][1] + 3.0,
        checkpoints=(pulse_start - 0.2, pulse_end + 0.5),
    )
    (_, before, t_before), (_, after, t_after) = snapshots
    assert before["timer_state"] == after["timer_state"] == "running"
    drop = before["remaining"] - after["remaining"]
    assert abs(drop - (t_after - t_before)) < 0.06, (drop, t_after - t_before)
    assert after["flicker_count"] == 1, after["flicker_count"]
    assert len(tracker.raw_durations) == len(cycles) - 1
    assert all(abs(value - 6.0) < 0.06 for value in tracker.raw_durations)
    estimate = tracker.estimate()
    assert estimate["mode"] == "fixed" and abs(estimate["period"] - 6.0) < 0.05, estimate
    print("OK  clignotement : le décompte continue (%.3f s écoulées / %.3f s retirées)" % (t_after - t_before, drop))


def test_prediction_tracks_next_extinction():
    off, cycles = build([6.0] * 8)
    tracker = LampCycleTracker("1")
    checkpoints = [relight + 0.5 for relight, _ in cycles[3:-1]]
    snapshots = run_tracker(tracker, off, cycles[-1][1] + 3.0, checkpoints=checkpoints)
    errors = []
    for (_, snapshot, sampled_at), (_, extinction) in zip(snapshots, cycles[3:-1]):
        errors.append(abs(sampled_at + snapshot["remaining"] - extinction))
    assert errors and max(errors) < 0.06, errors
    print("OK  prédiction de la prochaine extinction : erreur max %.0f ms" % (max(errors) * 1000))


def test_cutoff_duration_separates_flicker_from_extinction():
    off, cycles = build([5.0] * 4, blackout=0.78, flickers=((-1.5, 0.65),))
    tracker = LampCycleTracker("1")
    run_tracker(tracker, off, cycles[-1][1] + 3.0)
    assert len(tracker.events) == len(cycles)
    assert all(abs(value - 5.0) < 0.06 for value in tracker.raw_durations)
    kinds = [is_extinction for _, is_extinction in tracker.recent_offs]
    assert kinds.count(True) == len(cycles) and kinds.count(False) == len(cycles), tracker.recent_offs
    print("OK  coupure de 0,65 s = clignotement ; 0,78 s = extinction")


def test_extinction_without_flicker():
    off, cycles = build([6.0] * 5, flickers=())
    tracker = LampCycleTracker("1")
    run_tracker(tracker, off, cycles[-1][1] + 3.0)
    snapshot = tracker.snapshot(cycles[-1][1] + 3.0)
    assert snapshot["event_count"] == len(cycles)
    assert abs(snapshot["period"] - 6.0) < 0.05
    assert snapshot["flicker_count"] == 0
    print("OK  extinction sans clignotement préalable")


def test_random_delay_gives_a_window_that_contains_the_truth():
    rng = random.Random(7)
    delays = [rng.uniform(4.25, 7.26) for _ in range(14)]
    off, cycles = build(delays)
    tracker = LampCycleTracker("1")
    checkpoints = [relight + 0.5 for relight, _ in cycles[3:-1]]
    snapshots = run_tracker(tracker, off, cycles[-1][1] + 3.0, checkpoints=checkpoints)
    inside = 0
    for (_, snapshot, sampled_at), (_, extinction) in zip(snapshots, cycles[3:-1]):
        truth = extinction - sampled_at
        if snapshot["remaining_min"] - 0.3 <= truth <= snapshot["remaining_max"] + 0.3:
            inside += 1
    assert tracker.snapshot(cycles[-1][1] + 3.0)["prediction_mode"] == "window"
    assert inside == len(snapshots), "%d/%d fenêtres contiennent le vrai instant" % (inside, len(snapshots))
    print("OK  délai variable : %d/%d fenêtres contiennent le vrai instant" % (inside, len(snapshots)))


def test_measurement_gap_does_not_invent_durations():
    off, cycles = build([6.0] * 6)
    relight, _ = cycles[2]
    tracker = LampCycleTracker("1")
    run_tracker(tracker, off, cycles[-1][1] + 3.0, gaps=((relight + 1.0, relight + 1.9),))
    assert all(abs(value - 6.0) < 0.08 for value in tracker.raw_durations)
    assert len(tracker.raw_durations) >= 3
    print("OK  interruption de mesure : aucune durée faussée")


def test_start_during_blackout_waits_for_relight_anchor():
    off, cycles = build([6.0] * 5, blackout=3.0)
    tracker = LampCycleTracker("1")
    run_tracker(tracker, off, cycles[-1][1] + 3.0, start_at=cycles[1][0] - 1.5)
    assert len(tracker.raw_durations) >= 3
    assert all(abs(value - 6.0) < 0.08 for value in tracker.raw_durations)
    print("OK  démarrage pendant une extinction : ancrage au rallumage")


def test_static_lamps_do_not_hide_dynamic_extinctions():
    off, cycles = build([6.0] * 6)
    tracker = LampCycleTracker("1")
    run_tracker(tracker, off, cycles[-1][1] + 3.0, lamps=6, static=8)
    snapshot = tracker.snapshot(cycles[-1][1] + 3.0)
    assert snapshot["event_count"] >= len(cycles) - 1
    assert snapshot["dynamic_lamps"] == 6
    print("OK  lampes décoratives statiques ignorées")


def test_wrong_player_count_is_visible_and_recoverable():
    off, cycles = build([4.4] * 6)
    tracker = LampCycleTracker("3")
    run_tracker(tracker, off, cycles[-1][1] + 3.0)
    snapshot = tracker.snapshot(cycles[-1][1] + 3.0)
    assert snapshot["rejected_count"] == len(cycles) - 1
    tracker.set_players("1")
    snapshot = tracker.snapshot(cycles[-1][1] + 3.0)
    assert snapshot["rejected_count"] == 0
    assert snapshot["prediction_mode"] == "fixed" and abs(snapshot["period"] - 4.4) < 0.05
    print("OK  mesures hors plage récupérées après correction du nombre de joueurs")


def test_short_40ms_pulses_are_detected_and_counted():
    off, cycles = build([5.0] * 5, flickers=((-3.0, 0.04), (-2.5, 0.04), (-2.0, 0.04)))
    tracker = LampCycleTracker("1")
    checkpoint = cycles[-1][1] - 1.0
    snapshots = run_tracker(tracker, off, cycles[-1][1] + 3.0, dt=0.01, checkpoints=(checkpoint,))
    assert snapshots[0][1]["flicker_count"] == 3, snapshots[0][1]["flicker_count"]
    assert all(abs(value - 5.0) < 0.06 for value in tracker.raw_durations)
    print("OK  impulsions de 40 ms détectées sans relancer le décompte")


def test_scanner_sampling_path_with_fake_memory():
    clock = [100.0]
    lm.time.perf_counter = lambda: clock[0]
    off, cycles = build([6.0] * 5)
    
    def intensity(index, timestamp):
        return 0.0 if any(a <= timestamp - index * 0.004 < b for a, b in off) else 100.0

    class FakeMemory:
        def read(self, address, size):
            index = address - 0x10
            return struct.pack("<f", intensity(index, clock[0] - 100.0))

    scanner = lm.LampMemoryScanner()
    scanner.running, scanner.in_bottomfloor, scanner.world_name = True, True, "BottomFloor"
    scanner.cache = [{"actor": 0x1000 + i, "component": i, "offset": 0x10} for i in range(8)]
    scanner.lamps = {
        0x1000 + i: {"name": "BP_Lamp_C_%d" % i, "class": "BP_Lamp_C",
                     "actor": "%X" % (0x1000 + i), "intensity": None, "read_status": "OK"}
        for i in range(8)
    }
    memory = FakeMemory()
    end = cycles[-1][1] + 3.0
    while clock[0] - 100.0 < end:
        clock[0] += 0.012
        scanner._sample_cached(memory)
    snapshot = scanner.snapshot()
    json.dumps(snapshot)
    for key in ("lamp_state", "lamp_state_key", "timer_state", "remaining", "period_label",
                "quality", "event_count", "lamps", "stats", "flicker_count", "recent_offs"):
        assert key in snapshot, key
    assert snapshot["event_count"] == len(cycles) and abs(snapshot["period"] - 6.0) < 0.06, snapshot
    assert snapshot["lamps"][0]["on"] is True
    scanner.in_bottomfloor, scanner.world_name = False, "Level0"
    assert scanner.snapshot()["lamp_state"] == "Hors BottomFloor"
    print("OK  scanner -> tracker -> snapshot avec mémoire simulée")


def main():
    tests = (
        test_flickers_do_not_restart_countdown_or_calibration,
        test_prediction_tracks_next_extinction,
        test_cutoff_duration_separates_flicker_from_extinction,
        test_extinction_without_flicker,
        test_random_delay_gives_a_window_that_contains_the_truth,
        test_measurement_gap_does_not_invent_durations,
        test_start_during_blackout_waits_for_relight_anchor,
        test_static_lamps_do_not_hide_dynamic_extinctions,
        test_wrong_player_count_is_visible_and_recoverable,
        test_short_40ms_pulses_are_detected_and_counted,
        test_scanner_sampling_path_with_fake_memory,
    )
    for test in tests:
        test()
    print("Tous les tests du prédicteur de lampes passent.")


if __name__ == "__main__":
    main()
