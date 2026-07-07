"""Unit tests for daemon/simulator.py — ActivitySimulator state machine.

Tests use injected clock and jitter functions to remain fully deterministic.
All timing uses a mutable-list clock (closure pattern) so individual tests
control elapsed time without sleeping.
"""

from collections.abc import Callable

import pytest

from daemon.simulator import ActivitySimulator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_clock(start: float = 0.0) -> list[float]:
    """Return a mutable clock list; set clock[0] to advance time."""
    return [start]


def fixed_jitter(value: float) -> Callable[[float, float], float]:
    """Return a jitter callable that always returns `value` regardless of a/b."""
    return lambda _a, _b: value


def _clock_reader(clock: list[float]) -> Callable[[], float]:
    """Return a zero-arg `now` callable bound to `clock` (loop-safe closure)."""
    return lambda: clock[0]


# ---------------------------------------------------------------------------
# 1. Fresh simulator starts in "active"
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_fresh_simulator_starts_active(self):
        """A newly created ActivitySimulator must begin in the 'active' state."""
        clock = make_clock()
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        assert sim.state == "active"


# ---------------------------------------------------------------------------
# 2-4. tick() drives the full active → idle → hidden → active cycle
# ---------------------------------------------------------------------------


class TestTickTransitions:
    def test_full_cycle_with_boundaries(self):
        """One deep pass through the whole state machine.

        Covers: no-transition mid-active-window, active→idle ('idle'),
        idle→hidden ('hidden', with a 120 s idle window so the idle
        duration is genuinely respected), and hidden→active which must
        return the 'unhide' sentinel — NOT 'active'.
        """
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,  # 60 s active window
            idle_min=2,  # 120 s idle window
            hidden_min=1,  # 60 s hidden window
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        assert sim.state == "active"

        # Halfway through the active window — no transition yet
        clock[0] = 30.0
        assert sim.tick() is None
        assert sim.state == "active"

        # Advance just past the 60-second active_duration
        clock[0] = 61.0
        result = sim.tick()
        assert result == "idle"
        assert sim.state == "idle"
        idle_entered = clock[0]

        # Advance past idle_duration (120 s) from when idle was entered
        clock[0] = idle_entered + 121.0
        result = sim.tick()
        assert result == "hidden"
        assert sim.state == "hidden"
        hidden_entered = clock[0]

        # hidden → active (expect "unhide" sentinel, NOT "active")
        clock[0] = hidden_entered + 61.0
        result = sim.tick()
        assert result == "unhide"
        assert sim.state == "active"


# ---------------------------------------------------------------------------
# 5. on_new_content() from idle resets to active and updates state_entered_at
# ---------------------------------------------------------------------------


class TestOnNewContentFromIdle:
    def test_on_new_content_from_idle_resets_to_active(self):
        """on_new_content() while idle transitions back to active."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )

        # Drive to idle
        clock[0] = 61.0
        sim.tick()
        assert sim.state == "idle"

        # Simulate new content arriving at t=90
        clock[0] = 90.0
        sim.on_new_content()

        assert sim.state == "active"
        # state_entered_at must have been updated so future tick() uses t=90 as baseline
        assert sim.state_entered_at == 90.0

    def test_on_new_content_from_idle_tick_uses_new_baseline(self):
        """After on_new_content(), tick() counts from the reset time."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )

        clock[0] = 61.0
        sim.tick()  # → idle
        clock[0] = 90.0
        sim.on_new_content()  # reset to active at t=90

        # t=100: only 10 s into new active window (60 s needed) — no transition
        clock[0] = 100.0
        assert sim.tick() is None
        assert sim.state == "active"

    def test_on_new_content_while_already_active_resets_clock(self):
        """on_new_content() while already 'active' MUST reset state_entered_at.

        Regression guard: previously the method was gated on ``state !=
        'active'`` and left the clock untouched when already active, so
        a long-running active session could transition to idle seconds
        after new content arrived — contradicting the docstring
        contract of "start the active window from now".
        """
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        # Already at t=50 in the 60 s active window — about to transition
        clock[0] = 50.0
        sim.on_new_content()

        # state_entered_at must be reset to 50.0 (unconditionally)
        assert sim.state == "active"
        assert sim.state_entered_at == 50.0

        # At t=100 — only 50 s since the reset, still below active_duration
        clock[0] = 100.0
        assert sim.tick() is None
        assert sim.state == "active"


# ---------------------------------------------------------------------------
# 6. on_new_content() from hidden resets to active
# ---------------------------------------------------------------------------


class TestOnNewContentFromHidden:
    def test_on_new_content_from_hidden_resets_to_active(self):
        """on_new_content() while hidden transitions back to active."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )

        # Drive to hidden
        clock[0] = 61.0
        sim.tick()  # → idle
        idle_entered = clock[0]
        clock[0] = idle_entered + 61.0
        sim.tick()  # → hidden
        assert sim.state == "hidden"

        clock[0] += 5.0
        sim.on_new_content()

        assert sim.state == "active"


# ---------------------------------------------------------------------------
# 6b. on_new_content() return value -- transition bool (F4)
# ---------------------------------------------------------------------------


class TestOnNewContentReturnsBool:
    def test_on_new_content_from_idle_returns_true(self):
        """on_new_content() from 'idle' is a transition; returns True."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        clock[0] = 61.0
        sim.tick()
        assert sim.state == "idle"
        result = sim.on_new_content()
        assert result is True

    def test_on_new_content_from_hidden_returns_true(self):
        """on_new_content() from 'hidden' is a transition; returns True."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        clock[0] = 61.0
        sim.tick()
        idle_at = clock[0]
        clock[0] = idle_at + 61.0
        sim.tick()
        assert sim.state == "hidden"
        result = sim.on_new_content()
        assert result is True

    def test_on_new_content_from_active_returns_false(self):
        """on_new_content() from 'active' is a clock-reset only; returns False."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        assert sim.state == "active"
        result = sim.on_new_content()
        assert result is False

    def test_on_new_content_from_active_still_resets_clock(self):
        """on_new_content() from 'active' resets state_entered_at even when False."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        clock[0] = 50.0
        result = sim.on_new_content()
        assert result is False
        assert sim.state == "active"
        assert sim.state_entered_at == 50.0
        # Verify tick() uses the new baseline -- 60 s from t=50 means no transition
        # until t=110; at t=100 (50 s elapsed) no transition should occur.
        clock[0] = 100.0
        assert sim.tick() is None
        assert sim.state == "active"


# ---------------------------------------------------------------------------
# 7. timeline_interval uses jitter in active and idle; 0 in hidden
# ---------------------------------------------------------------------------


class TestTimelineInterval:
    @pytest.mark.parametrize(
        ("transitions", "jitter_value", "expected_state", "expected_interval"),
        [
            pytest.param(0, 0.0, "active", 180.0, id="active-zero-jitter-180"),
            pytest.param(0, 10.0, "active", 190.0, id="active-max-jitter-190"),
            pytest.param(1, 0.0, "idle", 600.0, id="idle-zero-jitter-600"),
            pytest.param(2, 0.0, "hidden", 0, id="hidden-always-zero"),
        ],
    )
    def test_timeline_interval(
        self,
        transitions: int,
        jitter_value: float,
        expected_state: str,
        expected_interval: float,
    ) -> None:
        """timeline_interval = base + jitter in active/idle; always 0 in hidden."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=_clock_reader(clock),
            jitter=fixed_jitter(jitter_value),
        )
        for _ in range(transitions):
            clock[0] += 61.0
            sim.tick()
        assert sim.state == expected_state
        assert sim.timeline_interval == expected_interval


# ---------------------------------------------------------------------------
# 8. story_interval returns 30 / 300 / 0 for active / idle / hidden
# ---------------------------------------------------------------------------


class TestStoryInterval:
    @pytest.mark.parametrize(
        ("transitions", "jitter_value", "expected_state", "expected_interval"),
        [
            pytest.param(0, 0.0, "active", 30, id="active-zero-jitter-30"),
            pytest.param(1, 0.0, "idle", 300, id="idle-zero-jitter-300"),
            pytest.param(2, 0.0, "hidden", 0, id="hidden-zero-jitter-0"),
            pytest.param(0, 1.0, "active", 31.0, id="active-jitter-1.0-31.0"),
            pytest.param(1, 1.5, "idle", 301.5, id="idle-jitter-1.5-301.5"),
            pytest.param(2, 99.0, "hidden", 0.0, id="hidden-ignores-jitter-0.0"),
        ],
    )
    def test_story_interval(
        self,
        transitions: int,
        jitter_value: float,
        expected_state: str,
        expected_interval: float,
    ) -> None:
        """story_interval = base + jitter in active/idle; always 0 in hidden."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=_clock_reader(clock),
            jitter=fixed_jitter(jitter_value),
        )
        for _ in range(transitions):
            clock[0] += 61.0
            sim.tick()
        assert sim.state == expected_state
        assert sim.story_interval == expected_interval


# ---------------------------------------------------------------------------
# 9. should_poll is True for active+idle, False for hidden
# ---------------------------------------------------------------------------


class TestShouldPoll:
    @pytest.mark.parametrize(
        ("transitions", "expected_state", "expected_poll"),
        [
            pytest.param(0, "active", True, id="active-polls"),
            pytest.param(1, "idle", True, id="idle-polls"),
            pytest.param(2, "hidden", False, id="hidden-does-not-poll"),
        ],
    )
    def test_should_poll(
        self,
        transitions: int,
        expected_state: str,
        expected_poll: bool,
    ) -> None:
        """should_poll is True in active/idle, False in hidden."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=_clock_reader(clock),
            jitter=fixed_jitter(0.0),
        )
        for _ in range(transitions):
            clock[0] += 61.0
            sim.tick()
        assert sim.state == expected_state
        assert sim.should_poll is expected_poll


# ---------------------------------------------------------------------------
# 10. on_ws_event_during_hidden wakes from hidden for INTERRUPT_EVENTS only
# ---------------------------------------------------------------------------


class TestOnWsEventDuringHidden:
    def _drive_to_hidden(self, sim: ActivitySimulator, clock: list[float]) -> None:
        """Helper: drive sim to hidden state using the given mutable clock."""
        clock[0] = 61.0
        sim.tick()  # active → idle
        idle_at = clock[0]
        clock[0] = idle_at + 61.0
        sim.tick()  # idle → hidden
        assert sim.state == "hidden"

    def test_interrupt_event_wakes_from_hidden(self):
        """svc=5 type=1 (new message) is an INTERRUPT_EVENT and wakes hidden→active."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        self._drive_to_hidden(sim, clock)

        sim.on_ws_event_during_hidden(5, 1)

        assert sim.state == "active"

    def test_non_interrupt_event_stays_hidden(self):
        """svc=1 type=1 is NOT an INTERRUPT_EVENT; state remains hidden."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        self._drive_to_hidden(sim, clock)

        sim.on_ws_event_during_hidden(1, 1)

        assert sim.state == "hidden"

    def test_all_interrupt_events_wake_from_hidden(self):
        """All four INTERRUPT_EVENTS (5,1), (15,5), (2,7), (2,8) wake the simulator."""
        interrupt_events = [(5, 1), (15, 5), (2, 7), (2, 8)]

        for svc, evt in interrupt_events:
            clock = make_clock(0.0)
            # Capture the per-iteration clock via a helper so the zero-arg `now`
            # lambda binds this iteration's list (avoids B023) while still
            # type-checking against Callable[[], float].
            now = _clock_reader(clock)
            sim = ActivitySimulator(
                active_min=1,
                idle_min=1,
                hidden_min=1,
                now=now,
                jitter=fixed_jitter(0.0),
            )
            self._drive_to_hidden(sim, clock)
            sim.on_ws_event_during_hidden(svc, evt)
            assert sim.state == "active", (
                f"INTERRUPT_EVENT ({svc}, {evt}) should wake from hidden"
            )

    def test_on_ws_event_noop_when_not_hidden(self):
        """on_ws_event_during_hidden has no effect when not in hidden state."""
        clock = make_clock()
        sim = ActivitySimulator(
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        assert sim.state == "active"
        sim.on_ws_event_during_hidden(5, 1)
        assert sim.state == "active"  # still active, not reset


# ---------------------------------------------------------------------------
# 11. Custom durations are respected
# ---------------------------------------------------------------------------


class TestCustomDurations:
    def test_custom_active_duration(self):
        """active_min parameter is converted to seconds and respected by tick()."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=5,  # 300 s
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        # 299 s should NOT trigger transition
        clock[0] = 299.0
        assert sim.tick() is None
        assert sim.state == "active"

        # 301 s SHOULD trigger transition
        clock[0] = 301.0
        assert sim.tick() == "idle"

    def test_custom_idle_duration(self):
        """idle_min parameter is converted to seconds and respected by tick()."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=3,  # 180 s
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        clock[0] = 61.0
        sim.tick()  # active → idle
        idle_at = clock[0]

        # 179 s after idle entry — no transition
        clock[0] = idle_at + 179.0
        assert sim.tick() is None
        assert sim.state == "idle"

        # 181 s after idle entry — transition
        clock[0] = idle_at + 181.0
        assert sim.tick() == "hidden"

    def test_custom_hidden_duration(self):
        """hidden_min parameter is converted to seconds and respected by tick()."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=2,  # 120 s
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        clock[0] = 61.0
        sim.tick()  # → idle
        idle_at = clock[0]
        clock[0] = idle_at + 61.0
        sim.tick()  # → hidden
        hidden_at = clock[0]

        # 119 s into hidden — no transition
        clock[0] = hidden_at + 119.0
        assert sim.tick() is None
        assert sim.state == "hidden"

        # 121 s into hidden — 'unhide' sentinel
        clock[0] = hidden_at + 121.0
        result = sim.tick()
        assert result == "unhide"
        assert sim.state == "active"
