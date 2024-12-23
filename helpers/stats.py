"""Statistics Module"""

from helpers.timer import Timer
from textio import print_info


def print_timing_statistics(timer: Timer) -> None:
    """Prints timing statistics.

    :param timer: The timer object to print statistics for.
    :type timer: Timer
    """
    print_info(
        f"Total time elapsed: {timer.get_elapsed_time_str()}"
        f"\n{20 * ' '}Average time per request: {timer.get_average_time_str()}"
    )
