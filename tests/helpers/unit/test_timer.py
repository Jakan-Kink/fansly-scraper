"""Unit tests for helpers/timer.py"""

import time
from unittest.mock import MagicMock

import pytest

from helpers.timer import Timer, TimerError, timing_jitter


class TestTimingJitter:
    """Tests for the timing_jitter function."""

    def test_timing_jitter_returns_float(self):
        """Test timing_jitter returns a float."""
        result = timing_jitter(0.5, 1.5)
        assert isinstance(result, float)

    def test_timing_jitter_within_range(self):
        """Test timing_jitter returns value within specified range."""
        min_val, max_val = 1.0, 2.0
        for _ in range(100):
            result = timing_jitter(min_val, max_val)
            assert min_val <= result <= max_val

    def test_timing_jitter_negative_range(self):
        """Test timing_jitter with negative range."""
        result = timing_jitter(-2.0, -1.0)
        assert -2.0 <= result <= -1.0

    def test_timing_jitter_zero_range(self):
        """Test timing_jitter with same min and max."""
        result = timing_jitter(5.0, 5.0)
        assert result == 5.0


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

    def test_timer_format_time_seconds(self):
        """Test _format_time with seconds only."""
        result = Timer._format_time(45.0)
        assert result == "45s"

    def test_timer_format_time_minutes(self):
        """Test _format_time with minutes and seconds."""
        result = Timer._format_time(125.0)  # 2m 5s
        assert result == "2m 5s"

    def test_timer_format_time_hours(self):
        """Test _format_time with hours, minutes, and seconds."""
        result = Timer._format_time(3665.0)  # 1h 1m 5s
        assert result == "1h 1m 5s"

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
        """Test get_average_time_str."""
        Timer.timers.clear()
        timer = Timer(name="average_test")
        timer.start()
        time.sleep(0.01)
        timer.stop()
        avg_str = timer.get_average_time_str()
        assert "s" in avg_str

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
