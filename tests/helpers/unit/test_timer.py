"""Unit tests for helpers/timer.py"""

import time
from unittest.mock import MagicMock

import pytest

from helpers.timer import Timer, TimerError, timing_jitter


class TestTimingJitter:
    """Tests for the timing_jitter function."""

    @pytest.mark.parametrize(
        ("min_val", "max_val"),
        [
            pytest.param(0.5, 1.5, id="fractional-range"),
            pytest.param(1.0, 2.0, id="unit-range"),
            pytest.param(-2.0, -1.0, id="negative-range"),
            pytest.param(5.0, 5.0, id="zero-width-range-exact-value"),
        ],
    )
    def test_timing_jitter_type_and_range(self, min_val, max_val):
        """timing_jitter returns a float within [min_val, max_val].

        100 samples per row (from the original within-range test). The
        zero-width row implies the original ``result == 5.0`` assertion.
        """
        for _ in range(100):
            result = timing_jitter(min_val, max_val)
            assert isinstance(result, float)
            assert min_val <= result <= max_val


class TestTimerError:
    """Tests for the TimerError exception."""

    def test_timer_error_is_exception(self):
        """Test TimerError is an Exception."""
        assert issubclass(TimerError, Exception)

    def test_timer_error_can_be_raised(self):
        """Test TimerError can be raised with message."""
        with pytest.raises(TimerError, match="Test error"):
            raise TimerError("Test error")


class TestTimer:
    """Tests for the Timer class."""

    def test_timer_start_stop(self):
        """Test basic timer start and stop."""
        timer = Timer()
        timer.start()
        time.sleep(0.01)  # Small sleep to ensure elapsed time
        elapsed = timer.stop()
        assert elapsed >= 0.01
        assert elapsed < 1.0  # Should be very quick

    def test_timer_start_twice_raises_error(self):
        """Test starting timer twice raises TimerError."""
        timer = Timer()
        timer.start()
        with pytest.raises(TimerError, match="Timer is running"):
            timer.start()

    def test_timer_stop_without_start_raises_error(self):
        """Test stopping timer without starting raises TimerError."""
        timer = Timer()
        with pytest.raises(TimerError, match="Timer is not running"):
            timer.stop()

    def test_timer_as_context_manager(self):
        """Test Timer used as context manager."""
        with Timer() as timer:
            time.sleep(0.01)
        # Timer should have stopped automatically
        assert timer._start_time is None

    def test_timer_with_name(self):
        """Test Timer with name accumulates time."""
        Timer.timers.clear()  # Clear class variable
        timer1 = Timer(name="test")
        timer1.start()
        time.sleep(0.01)
        timer1.stop()

        timer2 = Timer(name="test")
        timer2.start()
        time.sleep(0.01)
        timer2.stop()

        # Both timers should accumulate to same name
        assert Timer.timers["test"] >= 0.02

    def test_timer_with_logger(self):
        """Test Timer with custom logger."""
        mock_logger = MagicMock()
        timer = Timer(logger=mock_logger)
        timer.start()
        time.sleep(0.01)
        timer.stop()
        # Logger should have been called
        mock_logger.assert_called_once()

    def test_timer_with_custom_text(self):
        """Test Timer with custom text format."""
        mock_logger = MagicMock()
        timer = Timer(text="Time taken: {:0.2f}s", logger=mock_logger)
        timer.start()
        time.sleep(0.01)
        timer.stop()
        # Check logger was called with formatted text
        call_args = mock_logger.call_args[0][0]
        assert "Time taken:" in call_args

    def test_timer_as_decorator(self):
        """Test Timer as decorator using ContextDecorator."""

        @Timer()
        def slow_function():
            time.sleep(0.01)
            return "done"

        result = slow_function()
        assert result == "done"

    @pytest.mark.parametrize(
        ("elapsed", "expected"),
        [
            pytest.param(45.0, "45s", id="seconds-only"),
            pytest.param(125.0, "2m 5s", id="minutes-and-seconds"),
            pytest.param(3665.0, "1h 1m 5s", id="hours-minutes-seconds"),
        ],
    )
    def test_timer_format_time(self, elapsed, expected):
        """_format_time picks the seconds/minutes/hours branch by magnitude."""
        assert Timer._format_time(elapsed) == expected

    def test_get_elapsed_time_str_with_name(self):
        """Test get_elapsed_time_str with named timer."""
        Timer.timers.clear()
        timer = Timer(name="elapsed_test")
        timer.start()
        time.sleep(1.0)  # Sleep 1 second to ensure formatted time is not "0s"
        timer.stop()
        elapsed_str = timer.get_elapsed_time_str()
        assert "s" in elapsed_str
        assert elapsed_str != "0s"

    def test_get_elapsed_time_str_without_name(self):
        """Test get_elapsed_time_str without named timer."""
        timer = Timer()
        elapsed_str = timer.get_elapsed_time_str()
        assert elapsed_str == "0s"

    def test_get_average_time_str(self):
        """Average with recorded data, then the not-in-dict fallback (line 133).

        One walk replacing three tests: the 0.01s-sleep variant's ``"s" in``
        assertion is subsumed by the 1s-sleep assertions here; deleting the
        name from the dict afterwards exercises the "0s" fallback branch.
        """
        Timer.timers.clear()
        timer = Timer(name="average_test")
        timer.start()
        time.sleep(1.0)  # Need at least 1 second since int(elapsed) rounds down
        timer.stop()

        result = timer.get_average_time_str()
        # Should be at least "1s" since we slept for 1 second
        assert result != "0s"
        # Should be a formatted time string with seconds
        assert "s" in result

        # Remove from timers dict to test the edge case (line 133)
        del Timer.timers["average_test"]
        assert timer.get_average_time_str() == "0s"

    def test_get_all_timers_str(self):
        """Test get_all_timers_str class method."""
        Timer.timers.clear()
        timer1 = Timer(name="creator1")
        timer1.start()
        time.sleep(0.01)
        timer1.stop()

        timer2 = Timer(name="creator2")
        timer2.start()
        time.sleep(0.01)
        timer2.stop()

        timer_total = Timer(name="Total")
        timer_total.start()
        time.sleep(0.01)
        timer_total.stop()

        all_timers_str = Timer.get_all_timers_str()
        assert "SESSION DURATION" in all_timers_str
        assert "@creator1" in all_timers_str
        assert "@creator2" in all_timers_str
        assert "Total execution time" in all_timers_str

    def test_get_all_timers_str_without_total(self):
        """Test get_all_timers_str without Total timer."""
        Timer.timers.clear()
        timer = Timer(name="single")
        timer.start()
        time.sleep(0.01)
        timer.stop()

        all_timers_str = Timer.get_all_timers_str()
        assert "SESSION DURATION" in all_timers_str
        assert "@single" in all_timers_str
        assert "Total execution time" not in all_timers_str

    def test_timer_post_init_adds_to_timers_dict(self):
        """Test that __post_init__ adds timer to class dict."""
        Timer.timers.clear()
        timer = Timer(name="init_test")
        assert "init_test" in Timer.timers
        assert Timer.timers["init_test"] == 0
