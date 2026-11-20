"""Tests synthétiques du prédicteur BottomFloor, sans connexion au jeu.

Lancer depuis ETB_Manager :
    python -m unittest discover -s modules/garage/tests -v
"""
from __future__ import annotations

import logging
import random
import sys
import unittest
from pathlib import Path

# Le fichier est dans ETB_Manager/modules/garage/tests : on ajoute ETB_Manager au PYTHONPATH.
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.garage.lamp_memory import LampCycleTracker  # noqa: E402

logging.disable(logging.CRITICAL)

FLICKERS = ((-2.2, 0.12), (-1.4, 0.10))


def build(delays, blackout=2.0, flickers=FLICKERS, start=0.5):
    """Construit les intervalles OFF et les couples (rallumage, extinction)."""
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


class LampCycleTests(unittest.TestCase):
    def test_short_flicker_does_not_restart_countdown_or_calibration(self):
        off, cycles = build([6.0] * 7)
        tracker = LampCycleTracker("1")
        extinction = cycles[4][1]
        pulse_start, pulse_end = extinction + FLICKERS[0][0], extinction + FLICKERS[0][0] + FLICKERS[0][1]
        snapshots = run_tracker(
            tracker, off, cycles[-1][1] + 3.0,
            checkpoints=(pulse_start - 0.2, pulse_end + 0.5),
        )
        (_, before, t_before), (_, after, t_after) = snapshots
        self.assertEqual(before["timer_state"], "running")
        self.assertEqual(after["timer_state"], "running")
        self.assertAlmostEqual(before["remaining"] - after["remaining"], t_after - t_before, delta=0.06)
        self.assertEqual(after["flicker_count"], 1)
        self.assertEqual(len(tracker.raw_durations), len(cycles) - 1)
        self.assertTrue(all(abs(value - 6.0) < 0.06 for value in tracker.raw_durations))
        estimate = tracker.estimate()
        self.assertEqual(estimate["mode"], "fixed")
        self.assertAlmostEqual(estimate["period"], 6.0, delta=0.05)

    def test_prediction_tracks_next_extinction(self):
        off, cycles = build([6.0] * 8)
        tracker = LampCycleTracker("1")
        checkpoints = [relight + 0.5 for relight, _ in cycles[3:-1]]
        snapshots = run_tracker(tracker, off, cycles[-1][1] + 3.0, checkpoints=checkpoints)
        errors = []
        for (_, snapshot, sampled_at), (_, extinction) in zip(snapshots, cycles[3:-1]):
            errors.append(abs(sampled_at + snapshot["remaining"] - extinction))
        self.assertTrue(errors)
        self.assertLess(max(errors), 0.06)

    def test_cutoff_duration_separates_flicker_from_extinction(self):
        off, cycles = build([5.0] * 4, blackout=0.78, flickers=((-1.5, 0.65),))
        tracker = LampCycleTracker("1")
        run_tracker(tracker, off, cycles[-1][1] + 3.0)
        self.assertEqual(len(tracker.events), len(cycles))
        self.assertTrue(all(abs(value - 5.0) < 0.06 for value in tracker.raw_durations))
        kinds = [is_extinction for _, is_extinction in tracker.recent_offs]
        self.assertEqual(kinds.count(True), len(cycles))
        self.assertEqual(kinds.count(False), len(cycles))

    def test_extinction_without_flicker(self):
        off, cycles = build([6.0] * 5, flickers=())
        tracker = LampCycleTracker("1")
        run_tracker(tracker, off, cycles[-1][1] + 3.0)
        self.assertEqual(len(tracker.events), len(cycles))
        self.assertAlmostEqual(tracker.estimate()["period"], 6.0, delta=0.05)
        self.assertEqual(tracker.snapshot(cycles[-1][1] + 3.0)["flicker_count"], 0)

    def test_measurement_gap_does_not_create_false_duration(self):
        off, cycles = build([6.0] * 6)
        relight, _ = cycles[2]
        tracker = LampCycleTracker("1")
        run_tracker(tracker, off, cycles[-1][1] + 3.0, gaps=((relight + 1.0, relight + 1.9),))
        self.assertTrue(all(abs(value - 6.0) < 0.08 for value in tracker.raw_durations))
        self.assertGreaterEqual(len(tracker.raw_durations), 3)

    def test_start_during_blackout_waits_for_relight_anchor(self):
        off, cycles = build([6.0] * 5, blackout=3.0)
        tracker = LampCycleTracker("1")
        run_tracker(tracker, off, cycles[-1][1] + 3.0, start_at=cycles[1][0] - 1.5)
        self.assertGreaterEqual(len(tracker.raw_durations), 3)
        self.assertTrue(all(abs(value - 6.0) < 0.08 for value in tracker.raw_durations))

    def test_static_lamps_do_not_hide_dynamic_lamp_extinctions(self):
        off, cycles = build([6.0] * 6)
        tracker = LampCycleTracker("1")
        run_tracker(tracker, off, cycles[-1][1] + 3.0, lamps=6, static=8)
        snapshot = tracker.snapshot(cycles[-1][1] + 3.0)
        self.assertGreaterEqual(snapshot["event_count"], len(cycles) - 1)
        self.assertEqual(snapshot["dynamic_lamps"], 6)

    def test_wrong_player_count_can_be_corrected_retroactively(self):
        off, cycles = build([4.4] * 6)
        tracker = LampCycleTracker("3")
        run_tracker(tracker, off, cycles[-1][1] + 3.0)
        self.assertEqual(tracker.snapshot(cycles[-1][1] + 3.0)["rejected_count"], len(cycles) - 1)
        tracker.set_players("1")
        snapshot = tracker.snapshot(cycles[-1][1] + 3.0)
        self.assertEqual(snapshot["rejected_count"], 0)
        self.assertEqual(snapshot["prediction_mode"], "fixed")
        self.assertAlmostEqual(snapshot["period"], 4.4, delta=0.05)


if __name__ == "__main__":
    unittest.main()
