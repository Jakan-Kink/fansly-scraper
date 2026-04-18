"""Unit tests for daemon/simulator.py — ActivitySimulator state machine.

Tests use injected clock and jitter functions to remain fully deterministic.
All timing uses a mutable-list clock (closure pattern) so individual tests
control elapsed time without sleeping.
"""

from daemon.simulator import ActivitySimulator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_clock(start: float = 0.0) -> list[float]:
    """Return a mutable clock list; set clock[0] to advance time."""
    return [start]


def fixed_jitter(value: float):
    """Return a jitter callable that always returns `value` regardless of a/b."""
    return lambda _a, _b: value


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
# 2. tick() transitions active → idle after active_duration seconds elapse
# ---------------------------------------------------------------------------


class TestTickActiveToIdle:
    def test_active_to_idle_after_duration(self):
        """tick() returns 'idle' and updates state when active_duration elapses."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,  # 60 s active window
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        assert sim.state == "active"

        # Advance just past the 60-second active_duration
        clock[0] = 61.0
        result = sim.tick()

        assert result == "idle"
        assert sim.state == "idle"

    def test_tick_returns_none_before_active_duration(self):
        """tick() returns None while still within the active window."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        clock[0] = 30.0  # halfway through active window
        assert sim.tick() is None
        assert sim.state == "active"


# ---------------------------------------------------------------------------
# 3. tick() transitions idle → hidden after idle_duration
# ---------------------------------------------------------------------------


class TestTickIdleToHidden:
    def test_idle_to_hidden_after_duration(self):
        """tick() returns 'hidden' and updates state when idle_duration elapses."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,  # 60 s
            idle_min=2,  # 120 s
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )

        # Drive active → idle
        clock[0] = 61.0
        assert sim.tick() == "idle"

        # Capture when idle was entered
        idle_entered = clock[0]

        # Advance past idle_duration (120 s) from when idle was entered
        clock[0] = idle_entered + 121.0
        result = sim.tick()

        assert result == "hidden"
        assert sim.state == "hidden"


# ---------------------------------------------------------------------------
# 4. tick() transitions hidden → active and returns "unhide"
# ---------------------------------------------------------------------------


class TestTickHiddenToActive:
    def test_hidden_to_active_returns_unhide(self):
        """tick() returns the sentinel 'unhide' when hidden_duration elapses."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,  # 60 s hidden window
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )

        # active → idle
        clock[0] = 61.0
        assert sim.tick() == "idle"
        idle_entered = clock[0]

        # idle → hidden
        clock[0] = idle_entered + 61.0
        assert sim.tick() == "hidden"
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
    def test_timeline_interval_active_zero_jitter(self):
        """Active timeline_interval = 180 + jitter(0,10). With jitter=0 → exactly 180."""
        clock = make_clock()
        sim = ActivitySimulator(
            now=lambda: clock[0],
            jitter=fixed_jitter(0.0),
        )
        assert sim.state == "active"
        assert sim.timeline_interval == 180.0

    def test_timeline_interval_active_max_jitter(self):
        """With jitter always returning 10, active timeline_interval = 190."""
        clock = make_clock()
        sim = ActivitySimulator(
            now=lambda: clock[0],
            jitter=fixed_jitter(10.0),
        )
        assert sim.timeline_interval == 190.0

    def test_timeline_interval_idle_zero_jitter(self):
        """Idle timeline_interval = 600 + jitter(0,10). With jitter=0 → exactly 600."""
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
        assert sim.state == "idle"
        assert sim.timeline_interval == 600.0

    def test_timeline_interval_hidden_is_zero(self):
        """Hidden timeline_interval is always 0 (no polling)."""
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
        idle_at = clock[0]
        clock[0] = idle_at + 61.0
        sim.tick()  # → hidden
        assert sim.state == "hidden"
        assert sim.timeline_interval == 0


# ---------------------------------------------------------------------------
# 8. story_interval returns 30 / 300 / 0 for active / idle / hidden
# ---------------------------------------------------------------------------


class TestStoryInterval:
    def test_story_interval_active(self):
        """Active story_interval is 30 + jitter. With jitter=0.0 -> exactly 30."""
        clock = make_clock()
        sim = ActivitySimulator(now=lambda: clock[0], jitter=fixed_jitter(0.0))
        assert sim.story_interval == 30

    def test_story_interval_idle(self):
        """Idle story_interval is 300 + jitter. With jitter=0.0 -> exactly 300."""
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
        assert sim.story_interval == 300

    def test_story_interval_hidden(self):
        """Hidden story_interval is 0 (no jitter, no polling)."""
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
        assert sim.story_interval == 0

    def test_story_interval_active_with_jitter(self):
        """Active story_interval = 30 + injected jitter. jitter=1.0 -> 31.0."""
        clock = make_clock()
        sim = ActivitySimulator(now=lambda: clock[0], jitter=fixed_jitter(1.0))
        assert sim.state == "active"
        assert sim.story_interval == 31.0

    def test_story_interval_idle_with_jitter(self):
        """Idle story_interval = 300 + injected jitter. jitter=1.5 -> 301.5."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(1.5),
        )
        clock[0] = 61.0
        sim.tick()
        assert sim.state == "idle"
        assert sim.story_interval == 301.5

    def test_story_interval_hidden_no_jitter(self):
        """Hidden story_interval is always 0 regardless of jitter value."""
        clock = make_clock(0.0)
        sim = ActivitySimulator(
            active_min=1,
            idle_min=1,
            hidden_min=1,
            now=lambda: clock[0],
            jitter=fixed_jitter(99.0),
        )
        clock[0] = 61.0
        sim.tick()
        idle_at = clock[0]
        clock[0] = idle_at + 61.0
        sim.tick()
        assert sim.state == "hidden"
        assert sim.story_interval == 0.0


# ---------------------------------------------------------------------------
# 9. should_poll is True for active+idle, False for hidden
# ---------------------------------------------------------------------------


class TestShouldPoll:
    def test_should_poll_active(self):
        """should_poll is True in active state."""
        clock = make_clock()
        sim = ActivitySimulator(now=lambda: clock[0], jitter=fixed_jitter(0.0))
        assert sim.should_poll is True

    def test_should_poll_idle(self):
        """should_poll is True in idle state."""
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
        assert sim.should_poll is True

    def test_should_poll_hidden(self):
        """should_poll is False in hidden state."""
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
        assert sim.should_poll is False


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
            sim = ActivitySimulator(
                active_min=1,
                idle_min=1,
                hidden_min=1,
                now=lambda c=clock: c[0],
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
