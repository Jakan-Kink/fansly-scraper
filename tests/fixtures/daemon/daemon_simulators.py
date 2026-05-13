"""Simulator-shaped fakes for daemon tests.

Both fakes target collaborator-replacement scenarios where the real
``daemon.simulator.ActivitySimulator`` is unsuitable:

* ``StubSimulator`` decouples ``should_poll`` from ``timeline_interval`` /
  ``story_interval`` so loop-guard branches can be hit. The real simulator
  ties both to ``state == "hidden"``, so production paths defending against
  a state-transition mid-loop iteration are otherwise unreachable in tests.

* ``RecordingSimulator`` exposes a single observable side-effect channel
  (``during_hidden_calls``) so unit tests of WebSocket-event handlers can
  assert on the simulator notification path without dragging in the full
  state machine.
"""

from __future__ import annotations


class StubSimulator:
    """Minimal simulator with overridable interval/should_poll/transition.

    Used to drive ``_timeline_poll_loop`` and ``_story_poll_loop`` into
    branches the real ActivitySimulator can't reach without external state
    transitions (e.g., ``should_poll=False`` with ``interval > 0``, or
    ``on_new_content`` returning True without sleeping through a real
    state transition).
    """

    def __init__(
        self,
        *,
        timeline_interval: float = 1.0,
        story_interval: float = 1.0,
        should_poll: bool = True,
        transitions: bool = False,
    ) -> None:
        self.timeline_interval = timeline_interval
        self.story_interval = story_interval
        self.should_poll = should_poll
        self._transitions = transitions
        self.state = "stub"

    def on_new_content(self) -> bool:
        return self._transitions


class RecordingSimulator:
    """Simulator stub that records ``on_ws_event_during_hidden`` calls.

    Used by ``_make_ws_handler`` tests to assert on the simulator
    notification side-effect (svc/event_type tuple) without standing up
    the full ActivitySimulator state machine.
    """

    def __init__(self) -> None:
        self.during_hidden_calls: list[tuple] = []

    def on_ws_event_during_hidden(self, service_id, event_type) -> bool:
        self.during_hidden_calls.append((service_id, event_type))
        return False
