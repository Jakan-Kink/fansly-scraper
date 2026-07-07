"""Web Utilities"""

from collections import OrderedDict
from typing import NamedTuple
from urllib.parse import parse_qs, urlparse, urlunparse

import httpx


def strip_url_params(url: str) -> str:
    """Strip all query parameters and fragments from a URL.

    Args:
        url: The URL to normalize

    Returns:
        The URL with query string and fragment removed
    """
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def get_file_name_from_url(url: str) -> str:
    """Parses an URL and returns the last part which usually is a
    file name or directory/section.

    :param url: The URL to parse.
    :type url: str

    :return: The last part of the path ie. everything after the
        last slash excluding the query string.
    :rtype: str
    """
    parsed_url = urlparse(url)

    last_part = parsed_url.path.split("/")[-1]

    return last_part


def get_qs_value(url: str, key: str, default: str | None = None) -> str | None:
    """Returns the value of a specific key of an URL query string.

    :param url: The URL to parse for a query string.
    :type url: str

    :param key: The key in the query string (&key1=value1&key2=value2 ...)
        whose value to return.
    :type key: str

    :param default: The default value to return if the
        key was not found.
    :type default: Optional[str]

    :return: The value of `key` in the query string or `default` otherwise.
    :rtype: Optional[str]
    """
    parsed_url = urlparse(url)
    qs = parsed_url.query
    parsed_qs = parse_qs(qs)

    if key not in parsed_qs:
        return default

    values = parsed_qs[key]
    if len(values) == 0:
        return None

    return values[0]


def get_flat_qs_dict(url: str) -> dict[str, str]:
    """Returns a flattened version of the dictionary
    as returned by `parse_qs`.

    :param url: The URL to parse for a query string.
    :type url: str

    :return: The dictionary as returned by `parse_qs` but with
        the values flattened (first element of list or `''`).
    :rtype: Any
    """
    query = parse_qs(urlparse(url).query)

    new_dict = OrderedDict()

    for key in query:
        value = query[key]

        if len(value) == 0:
            new_dict[key] = ""

        else:
            new_dict[key] = value[0]

    return new_dict


class SplitURL(NamedTuple):
    """Result of :func:`split_url` — base and file-name URLs."""

    base_url: str
    file_url: str


def split_url(url: str) -> SplitURL:
    """Splits an URL into absolue base and file name URLs
    without query strings et al.

    Eg.:
        https://my.server/some/path/interesting.txt?k1=v1&a2=b4

    becomes

        (
            base_url='https://my.server/some/path',
            file_url='https://my.server/some/path/interesting.txt'
        )
    """
    parsed_url = urlparse(url)

    # URL without query string et al
    file_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"

    # Base URL
    base_url = file_url.rsplit("/", 1)[0]

    return SplitURL(base_url, file_url)


def get_release_info_from_github(current_program_version: str) -> dict | None:
    """Fetches and parses the Fansly Downloader NG release info JSON from GitHub.

    :param current_program_version: The current program version to be
        used in the user agent of web requests.
    :type current_program_version: str

    :return: The release info from GitHub as dictionary or
        None if there where any complications eg. network error.
    :rtype: dict | None
    """
    try:
        url = "https://api.github.com/repos/prof79/fansly-downloader-ng/releases/latest"

        response = httpx.get(
            url,
            follow_redirects=True,
            headers={
                "user-agent": f"Fansly Downloader NG {current_program_version}",
                "accept-language": "en-US,en;q=0.9",
            },
            timeout=30.0,
        )

        response.raise_for_status()

    except Exception:
        return None

    if response.status_code != 200:
        return None

    return response.json()
