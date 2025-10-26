"""Browser Utilities"""

import contextlib
import webbrowser
from time import sleep


def open_url(url_to_open: str) -> None:
    """Opens an URL in a browser window.

    :param url_to_open: The URL to open in the browser.
    :type url_to_open: str
    """
    sleep(10)

    with contextlib.suppress(Exception):
        webbrowser.open(url_to_open, new=0, autoraise=True)


def open_get_started_url() -> None:
    """Opens the Getting Started URL in a browser window."""
    open_url("https://github.com/prof79/fansly-downloader-ng/wiki/Getting-Started")
