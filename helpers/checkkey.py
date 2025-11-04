"""CheckKey Extraction using AST-based back-walking.

This module extracts the Fansly checkKey by:
1. Downloading the Fansly homepage to find main.js URL
2. Downloading main.js
3. Using AST parsing to find assignments to this.checkKey_
4. Executing those assignments to get the actual value

NO REGEX FOR FINDING - uses structural AST traversal!
Uses JSPyBridge for efficient Python-JavaScript communication.
"""

import gc
import os
import re
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx

from config.logging import textio_logger


def _setup_nvm_environment() -> None:
    """Configure environment to use nvm's Node.js from .nvmrc.

    This sets up PATH and NODE_PATH to point to the nvm-managed Node.js
    version specified in .nvmrc before JSPyBridge imports, ensuring the
    project-specific Node.js environment is used.
    """
    # Check for nvm installation
    nvm_dir = os.environ.get("NVM_DIR") or str(Path.home() / ".nvm")
    nvm_path = Path(nvm_dir)

    if not nvm_path.exists():
        return

    # Check for .nvmrc in project root
    # Assuming this file is in helpers/, project root is parent directory
    project_root = Path(__file__).parent.parent
    nvmrc_path = project_root / ".nvmrc"

    node_version = None
    if nvmrc_path.exists():
        with suppress(Exception):
            node_version = nvmrc_path.read_text().strip()

    if node_version:
        # Use version from .nvmrc
        node_path = nvm_path / "versions" / "node" / node_version
    else:
        # Fall back to latest version
        versions_dir = nvm_path / "versions" / "node"
        if not versions_dir.exists():
            return
        versions = sorted(versions_dir.iterdir(), reverse=True)
        if not versions:
            return
        node_path = versions[0]

    if not node_path.exists():
        return

    node_bin = node_path / "bin"
    if not node_bin.exists():
        return

    # Add nvm's Node.js to PATH (prepend so it takes precedence)
    current_path = os.environ.get("PATH", "")
    if str(node_bin) not in current_path:
        os.environ["PATH"] = f"{node_bin}:{current_path}"

    # Set NODE_PATH for module resolution
    node_modules = node_path / "lib" / "node_modules"
    if node_modules.exists():
        os.environ["NODE_PATH"] = str(node_modules)

    # Set NVM_DIR if not already set
    if "NVM_DIR" not in os.environ:
        os.environ["NVM_DIR"] = nvm_dir


# Configure nvm environment before importing JSPyBridge
_setup_nvm_environment()

# Import JSPyBridge (required)
try:
    from javascript import eval_js, globalThis, require
except ImportError as e:
    textio_logger.error(
        f"JSPyBridge not available: {e}. Install with: poetry install && npm install -g acorn acorn-walk"
    )
    raise


def extract_checkkey_from_js(js_content: str) -> str | None:
    """Extract checkKey from JavaScript using AST parsing.

    This uses JSPyBridge with acorn to:
    1. Parse JavaScript into AST
    2. Find all assignments to this.checkKey_ structurally
    3. Execute those assignments to get values
    4. Return the first value (which is correct for fansly-client-check)

    NO REGEX for finding - pure AST traversal!

    :param js_content: The JavaScript content to parse
    :type js_content: str
    :return: The extracted checkKey value or None if extraction fails
    :rtype: str | None
    """
    # Import acorn modules as local variables to ensure cleanup after function exits
    acorn = require("acorn")
    acorn_walk = require("acorn-walk")

    try:
        # Log file size and preview
        textio_logger.debug(f"First 200 chars: {js_content[:50]}")

        # Parse JavaScript into AST
        textio_logger.debug("Starting AST parsing...")
        ast = acorn.parse(js_content, {"ecmaVersion": "latest", "sourceType": "script"})
        textio_logger.debug("AST parsing successful")

        # Find all assignments to this.checkKey_ (NO REGEX!)
        # These can be in AssignmentExpression OR within SequenceExpression
        assignments = []

        def check_node(node: Any, _state: Any = None) -> None:
            """Check if node is an assignment to this.checkKey_.

            Args:
                node: The AST node to check
                _state: State object passed by acorn-walk (unused)
            """
            nonlocal assignments
            # Check for direct assignment: this.checkKey_ = value
            # Use str() to convert JavaScript strings to Python strings for comparison
            # Use separate if statements (combining with 'and' doesn't work with JSPyBridge)
            # Skip nodes that don't have the expected structure
            with suppress(AttributeError, TypeError):
                if str(node.type) == "AssignmentExpression":  # noqa: SIM102
                    if str(node.left.type) == "MemberExpression":  # noqa: SIM102
                        if str(node.left.object.type) == "ThisExpression":  # noqa: SIM102
                            if str(node.left.property.type) == "Identifier":  # noqa: SIM102
                                if str(node.left.property.name) == "checkKey_":
                                    # Extract the expression from the source
                                    start = int(node.right.start)
                                    end = int(node.right.end)
                                    expression = js_content[start:end]
                                    assignments.append(
                                        {"position": start, "expression": expression}
                                    )

        # Walk the AST to find assignments
        # Check both direct AssignmentExpression and those within SequenceExpression
        assignment_count = [0]  # Use list for closure

        def count_assignments(node: Any, _state: Any = None) -> None:
            nonlocal assignment_count
            assignment_count[0] += 1
            check_node(node, _state)

        # Also check within SequenceExpression (for minified code like: a=1,b=2,c=3)
        def check_sequence(node: Any, _state: Any = None) -> None:
            nonlocal assignments, assignment_count
            with suppress(AttributeError, TypeError):
                # SequenceExpression has an 'expressions' array (JavaScript Proxy object)
                if hasattr(node, "expressions"):
                    expressions_array = node.expressions
                    # Use .length property to get array size
                    length = int(expressions_array.length)
                    # Iterate using index access
                    for idx in range(length):
                        expr = expressions_array[idx]
                        # Inline the check instead of calling check_node
                        # (nested callbacks don't work well with JSPyBridge async)
                        # Use separate if statements (combining with 'and' doesn't work with JSPyBridge)
                        with suppress(AttributeError, TypeError):
                            if str(expr.type) == "AssignmentExpression":  # noqa: SIM102
                                if str(expr.left.type) == "MemberExpression":  # noqa: SIM102
                                    if str(expr.left.object.type) == "ThisExpression":  # noqa: SIM102
                                        if str(expr.left.property.type) == "Identifier":  # noqa: SIM102
                                            if (
                                                str(expr.left.property.name)
                                                == "checkKey_"
                                            ):
                                                # Extract the expression from the source
                                                start = int(expr.right.start)
                                                end = int(expr.right.end)
                                                expression = js_content[start:end]
                                                assignments.append(
                                                    {
                                                        "position": start,
                                                        "expression": expression,
                                                    }
                                                )
                                                assignment_count[0] += 1

        textio_logger.debug("Starting AST walk...")
        acorn_walk.simple(
            ast,
            {
                "AssignmentExpression": count_assignments,
                "SequenceExpression": check_sequence,
            },
        )
        textio_logger.debug("AST walk completed")

        # Wait for JSPyBridge async callbacks to complete using monotonic time
        timeout_seconds = 30.0
        poll_interval = 0.1  # Check every 100ms
        start_time = time.monotonic()
        timeout_end = start_time + timeout_seconds

        textio_logger.debug(
            f"Waiting for JSPyBridge callbacks (timeout: {timeout_seconds}s)..."
        )
        while time.monotonic() < timeout_end:
            time.sleep(poll_interval)

            # Check if we found any assignments
            if len(assignments) > 0:
                elapsed = time.monotonic() - start_time
                textio_logger.debug(
                    f"Found {len(assignments)} assignments after {elapsed:.1f}s"
                )
                break

        textio_logger.debug(
            f"Finished waiting. Total assignments found: {len(assignments)}"
        )
        textio_logger.debug(f"Total assignment nodes checked: {assignment_count[0]}")

        # Additional wait to let any pending JSPyBridge callbacks complete
        # The AST walk triggers thousands of callbacks - give them time to drain
        textio_logger.debug("Waiting for pending callbacks to drain...")
        time.sleep(0.5)

        # Sort by position (first in file = first in execution)
        assignments.sort(key=lambda x: x["position"])

        if not assignments:
            textio_logger.warning(
                f"No assignments to this.checkKey_ found in JavaScript "
                f"(searched {assignment_count[0]} total assignments)"
            )
            # Cleanup before early return
            with suppress(Exception):
                del ast
                del acorn
                del acorn_walk
                gc.collect()
            return None

        # Execute the first assignment to get the value
        # Use JavaScript eval to execute the expression
        first_expression = assignments[0]["expression"]
        textio_logger.debug(f"First checkKey expression: {first_expression[:100]}...")

        # Normalize whitespace (beautified JS may have newlines)
        normalized_expression = " ".join(first_expression.split())
        textio_logger.debug(f"Evaluating expression: {normalized_expression[:100]}...")

        checkkey_value = eval_js(normalized_expression)
        textio_logger.debug(f"CheckKey extracted: {checkkey_value}")

        # Force JSPyBridge cleanup to prevent hanging callbacks
        textio_logger.debug("Forcing JSPyBridge cleanup...")
        try:
            # Clear references to JavaScript objects and callback functions
            del ast
            del assignments
            del count_assignments
            del check_sequence
            del acorn
            del acorn_walk

            # Force garbage collection to cleanup JSPyBridge proxies
            gc.collect()

            # Try to explicitly close the bridge connection
            with suppress(Exception):
                js_global = globalThis
                # Clear any pending timers or callbacks in the JS global scope
                if hasattr(js_global, "clearTimeout"):
                    # This won't affect Node.js process but signals intent
                    pass
                del js_global

            # Run GC again after clearing global refs
            gc.collect()

            # Delay to let bridge process cleanup pending callbacks
            # JSPyBridge needs time to flush async communication queue
            time.sleep(1.0)

            textio_logger.debug("JSPyBridge cleanup completed")
        except Exception as cleanup_error:
            textio_logger.debug(f"JSPyBridge cleanup warning: {cleanup_error}")

        return checkkey_value  # noqa: TRY300

    except Exception as e:
        textio_logger.error(f"JSPyBridge extraction error: {e}")
        # Cleanup on error path too (variables may not be defined if error occurred early)
        with suppress(Exception, NameError):
            # These may not exist if error occurred during import/parse
            del acorn  # noqa: F821
            del acorn_walk  # noqa: F821
        gc.collect()
        return None


def guess_check_key(user_agent: str) -> str | None:  # noqa: PLR0911
    """Tries to extract the check key from Fansly's main.js using AST parsing.

    This function:
    1. Downloads Fansly homepage to find main.js URL
    2. Downloads main.js
    3. Uses AST parsing to extract checkKey (NO REGEX for finding!)
    4. Falls back to hardcoded default if extraction fails

    Uses JSPyBridge for efficient JavaScript execution.

    :param user_agent: Browser user agent to use for requests
    :type user_agent: str
    :return: The check key string, or None if extraction fails completely
    :rtype: str | None
    """

    fansly_url = "https://fansly.com"

    # Default checkKey (current as of 2025-01-28)
    # This is: ["fySzis","oybZy8"].reverse().join("-")+"-bubayf"
    default_check_key = "oybZy8-fySzis-bubayf"

    headers = {
        "User-Agent": user_agent,
    }

    try:
        # Step 1: Download Fansly homepage to find main.js URL
        textio_logger.debug(f"Downloading Fansly homepage from {fansly_url}...")
        html_response = httpx.get(
            fansly_url,
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        )

        if html_response.status_code != 200:
            textio_logger.warning(
                f"Failed to download Fansly homepage: {html_response.status_code}"
            )
            return default_check_key

        textio_logger.debug(f"Homepage downloaded: {len(html_response.text)} bytes")

        # Find main.js URL using simple regex (only for finding the URL, not checkKey!)
        main_js_pattern = r'\ssrc\s*=\s*"(main\..*?\.js)"'
        main_js_match = re.search(
            pattern=main_js_pattern,
            string=html_response.text,
            flags=re.IGNORECASE | re.MULTILINE,
        )

        if not main_js_match:
            textio_logger.warning("Could not find main.js URL in Fansly homepage")
            return default_check_key

        main_js = main_js_match.group(1)
        main_js_url = f"{fansly_url}/{main_js}"
        textio_logger.debug(f"Found main.js URL: {main_js_url}")

        # Step 2: Download main.js
        textio_logger.debug(f"Downloading main.js from {main_js_url}...")
        js_response = httpx.get(
            main_js_url,
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        )

        if js_response.status_code != 200:
            textio_logger.warning(
                f"Failed to download main.js: {js_response.status_code}"
            )
            return default_check_key

        textio_logger.debug(f"main.js downloaded: {len(js_response.text)} bytes")

        # Step 3: Extract checkKey using AST parsing (NO REGEX!)
        # Uses JSPyBridge for JavaScript execution
        textio_logger.debug("Starting checkKey extraction...")
        checkkey = extract_checkkey_from_js(js_response.text)

        if checkkey:
            textio_logger.debug(f"Successfully extracted checkKey: {checkkey}")
            return checkkey

        # If AST extraction fails, fall back to default
        textio_logger.warning("AST extraction failed, using default checkKey")
        textio_logger.debug(f"Using default checkKey: {default_check_key}")
        return default_check_key  # noqa: TRY300

    except httpx.RequestError as e:
        textio_logger.error(f"Network error while downloading Fansly files: {e}", 4)
        return default_check_key

    except Exception as e:
        textio_logger.error(f"Unexpected error during checkKey extraction: {e}", 4)
        return default_check_key
