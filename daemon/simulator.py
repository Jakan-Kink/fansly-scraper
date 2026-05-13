"""ActivitySimulator — three-tier activity state machine for the monitoring daemon.

Cycles through three states to mimic real browser behaviour and avoid detection
as a bot by spacing API poll cadence realistically:

  active  — frequent polling (timeline every ~3 min, stories every 30 s)
  idle    — reduced polling (timeline every ~10 min, stories every 5 min)
  hidden  — no polling; WebSocket pings keep the connection alive

Transitions follow the sequence active → idle → hidden → active.
Certain high-priority WebSocket events (INTERRUPT_EVENTS) can break out of
the hidden state early, mimicking a user returning to the browser tab.

All timing is injected via the ``now`` and ``jitter`` callables so that unit
tests remain fully deterministic with a fake clock.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable


# Events that interrupt the hidden state and wake the simulator immediately.
# Each tuple is (service_id, event_type) as decoded from the WebSocket frame.
INTERRUPT_EVENTS: frozenset[tuple[int, int]] = frozenset(
    {
        (5, 1),  # MessageService — new DM received
        (15, 5),  # SubscriptionService — subscription confirmed
        (2, 7),  # MediaService — PPV media purchased
        (2, 8),  # MediaService — PPV bundle purchased
    }
)


class ActivitySimulator:
    """Three-tier activity simulator matching real browser behaviour.

    States: "active" → "idle" → "hidden" → "active" (cycle).
    Each state has configurable minimum durations (in minutes).
    The ``jitter`` callable is applied to timeline_interval to randomise
    poll cadence within a plausible human range.
    """

    def __init__(
        self,
        active_min: int = 60,
        idle_min: int = 120,
        hidden_min: int = 300,
        timeline_poll_active_seconds: int = 180,
        timeline_poll_idle_seconds: int = 600,
        story_poll_active_seconds: int = 30,
        story_poll_idle_seconds: int = 300,
        *,
        now: Callable[[], float] = time.monotonic,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        """Initialise the simulator.

        Args:
            active_min: Duration of the active phase in minutes.
            idle_min: Duration of the idle phase in minutes.
            hidden_min: Duration of the hidden phase in minutes.
            timeline_poll_active_seconds: Home-timeline poll interval while active.
            timeline_poll_idle_seconds: Home-timeline poll interval while idle.
            story_poll_active_seconds: Story-state poll interval while active.
            story_poll_idle_seconds: Story-state poll interval while idle.
            now: Callable returning the current time as a float (seconds).
                 Defaults to ``time.monotonic``; inject a fake clock in tests.
            jitter: Callable(low, high) returning a float in [low, high].
                    Defaults to ``random.uniform``; inject a fixed value in tests.
        """
        self.active_duration: float = active_min * 60.0
        self.idle_duration: float = idle_min * 60.0
        self.hidden_duration: float = hidden_min * 60.0

        self._timeline_poll_active: float = float(timeline_poll_active_seconds)
        self._timeline_poll_idle: float = float(timeline_poll_idle_seconds)
        self._story_poll_active: float = float(story_poll_active_seconds)
        self._story_poll_idle: float = float(story_poll_idle_seconds)

        self._now = now
        self._jitter = jitter

        self.state: str = "active"
        self.state_entered_at: float = self._now()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def timeline_interval(self) -> float:
        """Seconds between home-timeline polls for the current state.

        Configured base value plus jitter in active/idle; 0 in hidden.
        """
        if self.state == "active":
            return self._timeline_poll_active + self._jitter(0.0, 10.0)
        if self.state == "idle":
            return self._timeline_poll_idle + self._jitter(0.0, 10.0)
        return 0.0  # hidden — polling suspended

    @property
    def story_interval(self) -> float:
        """Seconds between story-state polls for the current state.

        Configured base value plus jitter in active/idle; 0 in hidden.
        """
        if self.state == "active":
            return self._story_poll_active + self._jitter(0.0, 2.0)
        if self.state == "idle":
            return self._story_poll_idle + self._jitter(0.0, 2.0)
        return 0.0  # hidden — polling suspended

    @property
    def should_poll(self) -> bool:
        """True when polling APIs is appropriate (active or idle); False when hidden."""
        return self.state != "hidden"

    # ------------------------------------------------------------------
    # State mutations
    # ------------------------------------------------------------------

    def on_new_content(self) -> bool:
        """Reset to active state when new content is discovered.

        Called by the polling loop or WebSocket handler whenever a post,
        story, or purchase is detected. Unconditionally resets
        ``state_entered_at`` so the active window starts from now -- this
        includes the case where the daemon is already in "active" but
        has been running long enough that the next ``tick()`` would
        otherwise transition to "idle" immediately.

        Returns:
            True if this was a TRANSITION (state was previously idle or hidden).
            False if the state was already "active" (clock-reset only).
        """
        was_non_active = self.state != "active"
        self.state = "active"
        self.state_entered_at = self._now()
        return was_non_active

    def on_ws_event_during_hidden(self, service_id: int, event_type: int) -> bool:
        """Wake from hidden state if the WebSocket event is an interrupt event.

        Mirrors the real browser behaviour: a desktop notification (e.g. new
        message) causes the user to switch back to the Fansly tab, resuming
        normal activity.

        Args:
            service_id: The ``svc`` field from the decoded WebSocket frame.
            event_type: The ``type`` field from the decoded WebSocket frame.

        Returns:
            True if a hidden → active wake occurred; False otherwise.
        """
        if self.state == "hidden" and (service_id, event_type) in INTERRUPT_EVENTS:
            self.state = "active"
            self.state_entered_at = self._now()
            return True
        return False

    def tick(self) -> str | None:
        """Advance the state machine by checking whether the current phase has expired.

        Should be called periodically (e.g. every poll loop iteration).

        Returns:
            - ``"idle"`` when active → idle transition occurs
            - ``"hidden"`` when idle → hidden transition occurs
            - ``"unhide"`` sentinel when hidden → active transition occurs
              (caller should call ``assertWebsocketConnection()`` before resuming)
            - ``None`` if no transition occurred
        """
        elapsed = self._now() - self.state_entered_at

        if self.state == "active" and elapsed > self.active_duration:
            self.state = "idle"
            self.state_entered_at = self._now()
            return "idle"

        if self.state == "idle" and elapsed > self.idle_duration:
            self.state = "hidden"
            self.state_entered_at = self._now()
            return "hidden"

        if self.state == "hidden" and elapsed > self.hidden_duration:
            self.state = "active"
            self.state_entered_at = self._now()
            return "unhide"

        return None
