"""Config fixtures for unit tests.

Names are deliberately distinct from the global ``config`` fixture in
``tests/fixtures/database/`` (DB-backed). Tests opt in by taking
``unit_config`` / ``unit_config_path`` as parameters; the database
``config`` continues to serve every other consumer unchanged.
"""

from .config_fixtures import (
    no_display,
    unit_config,
    unit_config_path,
    validation_config,
)


__all__ = [
    "no_display",
    "unit_config",
    "unit_config_path",
    "validation_config",
]
