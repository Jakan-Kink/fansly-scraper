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
    from javascript import connection, eval_js, globalThis, require
except ImportError as e:
    textio_logger.error(
        f"JSPyBridge not available: {e}. Install with: poetry install && npm install -g acorn acorn-walk"
    )
    raise


def _extract_expression_at_position(js_content: str, start_pos: int) -> str | None:
    """Extract a JavaScript expression starting at a given position.

    Uses bracket/paren/brace counting to find expression boundaries.
    Handles string literals and stops at statement terminators when nesting depth is 0.

    :param js_content: The JavaScript source code
    :type js_content: str
    :param start_pos: Position to start extracting from (after the '=')
    :type start_pos: int
    :return: The extracted expression or None if extraction fails
    :rtype: str | None
    """
    depth = 0  # Track nesting depth for (), [], {}
    pos = start_pos
    length = len(js_content)

    # Skip leading whitespace
    while pos < length and js_content[pos] in " \t\n\r":
        pos += 1

    if pos >= length:
        return None

    start = pos
    in_string = None  # Track if we're in a string (' or " or `)
    escape_next = False

    while pos < length:
        char = js_content[pos]

        # Handle escape sequences
        if escape_next:
            escape_next = False
            pos += 1
            continue

        if char == "\\" and in_string:
            escape_next = True
            pos += 1
            continue

        # Handle string literals
        if char in ('"', "'", "`"):
            if in_string == char:
                in_string = None
            elif in_string is None:
                in_string = char
            pos += 1
            continue

        # If we're in a string, skip all other processing
        if in_string:
            pos += 1
            continue

        # Track nesting depth for brackets/parens/braces
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth < 0:
                # Closing bracket for outer context - we're done
                break

        # Statement terminators at depth 0
        if depth == 0:
            if char in (";", "\n"):
                # End of expression
                break
            if char == ",":
                # Comma at depth 0 (sequence expression) - we're done
                break

        pos += 1

    # Extract the expression
    if pos > start:
        return js_content[start:pos].strip()

    return None


def _validate_checkkey_format(checkkey: str) -> bool:
    """Validate that checkKey matches expected format.

    Current expected format: "string-string-string"
    Example: "oybZy8-fySzis-bubayf"

    This validation detects if Fansly changes their checkKey generation.

    :param checkkey: The checkKey value to validate
    :type checkkey: str
    :return: True if format is valid
    :rtype: bool
    """
    # Must be a non-empty string
    if not isinstance(checkkey, str) or not checkkey:
        return False

    # Must contain hyphens (current format is dash-separated)
    if "-" not in checkkey:
        return False

    # Must have reasonable length (current is ~23 chars)
    # Allow range 10-50 to handle format variations
    if not (10 <= len(checkkey) <= 50):
        return False

    # Must be alphanumeric with hyphens only
    return all(c.isalnum() or c == "-" for c in checkkey)


def _extract_checkkey_regex(js_content: str) -> str | None:
    """Fast extraction using regex to find assignment positions.

    This is much faster than AST parsing (1900x) for minified code.
    Searches for 'this.checkKey_ = <expression>' patterns.

    Falls back to None if:
    - No assignments found
    - Expression extraction fails
    - Evaluation fails
    - Result doesn't pass validation

    :param js_content: The JavaScript content to parse
    :type js_content: str
    :return: The extracted checkKey value or None if extraction fails
    :rtype: str | None
    """
    checkkey_value = None

    try:
        # Find all occurrences of 'this.checkKey_' followed by optional whitespace and '='
        pattern = r"this\.checkKey_\s*="
        matches = list(re.finditer(pattern, js_content))

        if not matches:
            textio_logger.debug("Regex: No 'this.checkKey_' assignments found")
            return None

        textio_logger.debug(f"Regex: Found {len(matches)} potential assignment(s)")

        # Extract expressions at each position
        assignments = []
        for match in matches:
            # Position right after the '='
            expr_start = match.end()

            # Extract the expression
            expression = _extract_expression_at_position(js_content, expr_start)

            if not expression:
                textio_logger.debug(
                    f"Regex: Failed to extract expression at position {expr_start}"
                )
                continue

            assignments.append(
                {
                    "position": match.start(),
                    "expression": expression,
                }
            )

        if not assignments:
            textio_logger.debug("Regex: No valid expressions extracted")
            return None

        # Assignments are already sorted by position (regex finds them in order)
        # Use the first assignment (Fansly's real checkKey comes before decoys)
        first_expression = assignments[0]["expression"]

        # Normalize whitespace (beautified JS may have newlines)
        normalized_expression = " ".join(first_expression.split())

        # Try to evaluate the expression
        checkkey_value = eval_js(normalized_expression)

        # Validate format
        if not _validate_checkkey_format(checkkey_value):
            textio_logger.warning(
                f"Regex: Extracted checkKey failed validation: {checkkey_value}"
            )
            return None

        textio_logger.debug(f"Regex: Successfully extracted checkKey: {checkkey_value}")

    except Exception as e:
        textio_logger.debug(f"Regex: Extraction failed: {e}")
        return None
    else:
        return checkkey_value


def extract_checkkey_from_js(  # noqa: PLR0911
    js_content: str, expected_checkkey: str | None = None
) -> str | None:
    """Extract checkKey from JavaScript with hybrid regex+AST approach.

    Strategy:
    1. Try fast regex extraction
    2. If regex succeeds and matches expected → return (validated, fast path)
    3. If regex succeeds but doesn't match expected → run AST to verify
    4. If regex fails → run AST fallback

    This provides:
    - Fast extraction for normal operation (~1900x faster)
    - Safety verification when values mismatch (detects Fansly changes)
    - Robust fallback for edge cases

    :param js_content: The JavaScript content to parse
    :type js_content: str
    :param expected_checkkey: Expected checkKey value (from config or default) for validation
    :type expected_checkkey: str | None
    :return: The extracted checkKey value or None if extraction fails
    :rtype: str | None
    """
    # Try fast regex extraction first
    textio_logger.debug("Attempting fast regex extraction...")
    regex_checkkey = _extract_checkkey_regex(js_content)

    if regex_checkkey:
        # Regex succeeded - validate against expected value
        if expected_checkkey:
            if regex_checkkey == expected_checkkey:
                # Match! Fast path - trust the regex result
                textio_logger.debug(f"Regex matches expected value: {regex_checkkey} ✓")
                return regex_checkkey

            # Mismatch! Fansly may have changed their implementation
            # Run AST verification to determine which is correct
            textio_logger.warning(
                f"Regex checkKey mismatch! Regex: {regex_checkkey}, "
                f"Expected: {expected_checkkey}. Running AST verification..."
            )
            ast_checkkey = _extract_checkkey_ast_fallback(js_content)

            if ast_checkkey:
                if ast_checkkey == regex_checkkey:
                    textio_logger.warning(
                        f"AST confirms regex is correct: {ast_checkkey}. "
                        f"Expected value {expected_checkkey} is outdated!"
                    )
                    return ast_checkkey
                if ast_checkkey == expected_checkkey:
                    textio_logger.warning(
                        f"AST confirms expected is correct: {ast_checkkey}. "
                        f"Regex extraction failed validation!"
                    )
                    return ast_checkkey

                # AST returned different value from both - something is wrong
                textio_logger.error(
                    f"AST returned different value! Regex: {regex_checkkey}, "
                    f"Expected: {expected_checkkey}, AST: {ast_checkkey}"
                )
                # Trust AST as authoritative source
                return ast_checkkey

            # AST failed - trust regex if it passes format validation
            if _validate_checkkey_format(regex_checkkey):
                textio_logger.warning(
                    f"AST failed but regex passes validation: {regex_checkkey}"
                )
                return regex_checkkey

            # Both mismatched and AST failed - use expected as last resort
            textio_logger.warning(
                f"Regex/AST mismatch and AST failed. Using expected: {expected_checkkey}"
            )
            return expected_checkkey

        # No expected value - just validate format and return regex result
        if _validate_checkkey_format(regex_checkkey):
            textio_logger.debug(f"Regex extraction successful: {regex_checkkey}")
            return regex_checkkey

        textio_logger.warning(
            f"Regex result failed format validation: {regex_checkkey}"
        )
        # Fall through to AST

    # Regex failed or validation failed - use AST fallback
    textio_logger.debug("Regex extraction failed, falling back to AST parsing...")
    return _extract_checkkey_ast_fallback(js_content)


def _extract_checkkey_ast_fallback(js_content: str) -> str | None:
    """Extract checkKey from JavaScript using AST parsing (fallback method).

    This uses JSPyBridge with acorn to:
    1. Parse JavaScript into AST
    2. Find all assignments to this.checkKey_ structurally
    3. Execute those assignments to get values
    4. Return the first value (which is correct for fansly-client-check)

    This is slower than regex but handles edge cases where regex might fail.
    SequenceExpression callback removed - acorn_walk.simple() visits all nodes
    recursively, so assignments inside SequenceExpression are still found.

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
        # Only use AssignmentExpression callback - acorn_walk.simple() visits all nodes
        # recursively, so assignments inside SequenceExpression are still found!
        assignment_count = [0]  # Use list for closure

        def count_assignments(node: Any, _state: Any = None) -> None:
            nonlocal assignment_count
            assignment_count[0] += 1
            check_node(node, _state)

        textio_logger.debug("Starting AST walk...")

        # Track when AST walk completes (it runs synchronously but triggers async callbacks)
        walk_start = time.monotonic()
        acorn_walk.simple(
            ast,
            {
                "AssignmentExpression": count_assignments,
                # SequenceExpression callback removed - acorn_walk.simple() visits all
                # AssignmentExpression nodes recursively, regardless of parent context
            },
        )
        walk_elapsed = time.monotonic() - walk_start
        textio_logger.debug(f"AST walk completed in {walk_elapsed:.2f}s")

        # Wait for JSPyBridge async callbacks to complete
        # The walk itself is done, but callbacks are still being processed asynchronously
        # We need to wait for ALL callbacks to finish, not just until we find assignments
        timeout_seconds = 60.0  # Increased timeout for large JS files
        poll_interval = 0.5  # Check less frequently since we're waiting for completion
        start_time = time.monotonic()
        timeout_end = start_time + timeout_seconds

        textio_logger.debug(
            f"Waiting for all {assignment_count[0]} AST callbacks to complete (timeout: {timeout_seconds}s)..."
        )

        # Wait until we either find assignments or timeout
        # Don't break early - let all callbacks complete to avoid orphaned operations
        last_count = 0
        stable_iterations = 0
        while time.monotonic() < timeout_end:
            time.sleep(poll_interval)

            current_count = len(assignments)
            if current_count > 0 and current_count == last_count:
                # Count hasn't changed - callbacks might be complete
                stable_iterations += 1
                if stable_iterations >= 3:  # Stable for 3 iterations (1.5s)
                    elapsed = time.monotonic() - start_time
                    textio_logger.debug(
                        f"Found {len(assignments)} assignments after {elapsed:.1f}s (stable)"
                    )
                    break
            else:
                stable_iterations = 0

            last_count = current_count

        textio_logger.debug(
            f"Finished waiting. Total assignments found: {len(assignments)}"
        )
        textio_logger.debug(f"Total assignment nodes checked: {assignment_count[0]}")

        # Give extra time for any final stragglers to complete
        # Monitor the JSPyBridge communication queues to ensure they're empty
        textio_logger.debug("Waiting for JSPyBridge communication queues to drain...")
        drain_timeout = 10.0
        drain_start = time.monotonic()
        drain_end = drain_start + drain_timeout

        while time.monotonic() < drain_end:
            # Check if queues are empty
            send_queue_empty = len(connection.sendQ) == 0
            com_items_empty = len(connection.com_items) == 0

            if send_queue_empty and com_items_empty:
                elapsed = time.monotonic() - drain_start
                textio_logger.debug(
                    f"All JSPyBridge queues drained after {elapsed:.2f}s"
                )
                break

            time.sleep(0.1)
        else:
            # Timeout reached
            textio_logger.debug(
                f"Queue drain timeout after {drain_timeout}s "
                f"(sendQ: {len(connection.sendQ)}, com_items: {len(connection.com_items)})"
            )

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

        # Step 3: Extract checkKey using hybrid regex+AST approach
        # Uses JSPyBridge for JavaScript execution
        # Pass default_check_key for cross-validation
        textio_logger.debug("Starting checkKey extraction...")
        checkkey = extract_checkkey_from_js(
            js_response.text, expected_checkkey=default_check_key
        )

        if checkkey:
            textio_logger.debug(f"Successfully extracted checkKey: {checkkey}")
            return checkkey

        # If extraction fails, fall back to default
        textio_logger.warning("Extraction failed, using default checkKey")
        textio_logger.debug(f"Using default checkKey: {default_check_key}")
        return default_check_key  # noqa: TRY300

    except httpx.RequestError as e:
        textio_logger.error(f"Network error while downloading Fansly files: {e}", 4)
        return default_check_key

    except Exception as e:
        textio_logger.error(f"Unexpected error during checkKey extraction: {e}", 4)
        return default_check_key
