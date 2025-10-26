"""Cryptography Tests"""

import sys
from pathlib import Path

import pytest

from api import FanslyApi


sys.path.append(str(Path(__file__).parent.absolute().parent))


@pytest.mark.parametrize(
    "input_str, seed, expected",
    [
        ("a", 0, 7929297801672961),
        ("b", 0, 8684336938537663),
        ("revenge", 0, 4051478007546757),
        ("revenue", 0, 8309097637345594),
        ("revenue", 1, 8697026808958300),
        ("revenue", 2, 2021074995066978),
        ("revenue", 3, 4747903550515895),
    ],
)
def test_cyrb53(input_str, seed, expected):
    """Test cyrb53 hash function."""
    result = FanslyApi.cyrb53(input_str, seed)
    assert result == expected, "Digest doesn't match"


@pytest.mark.parametrize(
    "a, b, expected",
    [
        (-559038834, 2654435761, -1447829970),
        (1103547958, 1597334677, -1401873042),
        (3294967296, 1337, -1265170944),
    ],
)
def test_imul32(a, b, expected):
    """Test imul32 multiplication."""
    result = FanslyApi.imul32(a, b)
    assert result == expected, f"{a} * {b} should be {expected}, is {result}"
