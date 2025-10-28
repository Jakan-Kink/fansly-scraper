"""CheckKey Extraction using AST-based back-walking.

This module extracts the Fansly checkKey by:
1. Downloading the Fansly homepage to find main.js URL
2. Downloading main.js
3. Using AST parsing to find assignments to this.checkKey_
4. Executing those assignments to get the actual value

NO REGEX FOR FINDING - uses structural AST traversal!
Uses JSPyBridge for efficient Python-JavaScript communication.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
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

# Try to import JSPyBridge, fall back to subprocess if not available
try:
    from javascript import require

    acorn = require("acorn")
    acorn_walk = require("acorn-walk")
    HAS_JSPYBRIDGE = True
except ImportError:
    HAS_JSPYBRIDGE = False
    acorn = None
    acorn_walk = None


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

    if not HAS_JSPYBRIDGE:
        # Fallback to subprocess method
        return _extract_checkkey_subprocess(js_content)

    try:
        # Parse JavaScript into AST
        ast = acorn.parse(js_content, {"ecmaVersion": "latest", "sourceType": "script"})

        # Find all assignments to this.checkKey_ (NO REGEX!)
        # These can be in AssignmentExpression OR within SequenceExpression
        assignments = []

        def check_node(node: Any, _state: Any = None) -> None:
            """Check if node is an assignment to this.checkKey_.

            Args:
                node: The AST node to check
                _state: State object passed by acorn-walk (unused)
            """
            # Check for direct assignment: this.checkKey_ = value
            # Use str() to convert JavaScript strings to Python strings for comparison
            # Use separate if statements (combining with 'and' doesn't work with JSPyBridge)
            try:
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
            except (AttributeError, TypeError):
                # Skip nodes that don't have the expected structure
                pass

        # Walk the AST to find assignments
        # Check both direct AssignmentExpression and those within SequenceExpression
        assignment_count = [0]  # Use list for closure

        def count_assignments(node: Any, _state: Any = None) -> None:
            assignment_count[0] += 1
            check_node(node, _state)

        # Also check within SequenceExpression (for minified code like: a=1,b=2,c=3)
        def check_sequence(node: Any, _state: Any = None) -> None:
            try:
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
                        try:
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
                        except (AttributeError, TypeError):
                            pass
            except (AttributeError, TypeError):
                pass

        acorn_walk.simple(
            ast,
            {
                "AssignmentExpression": count_assignments,
                "SequenceExpression": check_sequence,
            },
        )

        # Wait for JSPyBridge async callbacks to complete
        # Poll for results with a timeout instead of fixed sleep
        import time

        timeout_seconds = 10.0
        poll_interval = 0.1  # Check every 100ms
        elapsed = 0.0

        while elapsed < timeout_seconds:
            time.sleep(poll_interval)
            elapsed += poll_interval

            # Check if we found any assignments
            if len(assignments) > 0:
                break

        # Sort by position (first in file = first in execution)
        assignments.sort(key=lambda x: x["position"])

        if not assignments:
            textio_logger.warning(
                f"No assignments to this.checkKey_ found in JavaScript "
                f"(searched {assignment_count[0]} total assignments)"
            )
            return None

        # Execute the first assignment to get the value
        # Use JavaScript eval to execute the expression
        from javascript import eval_js

        first_expression = assignments[0]["expression"]
        # Normalize whitespace (beautified JS may have newlines)
        normalized_expression = " ".join(first_expression.split())
        checkkey_value = eval_js(normalized_expression)

        return checkkey_value  # noqa: TRY300

    except Exception as e:
        textio_logger.warning(f"JSPyBridge extraction error: {e}")
        # Fallback to subprocess method
        return _extract_checkkey_subprocess(js_content)


def _extract_checkkey_subprocess(js_content: str) -> str | None:  # noqa: PLR0911
    """Fallback: Extract checkKey using subprocess if JSPyBridge fails.

    :param js_content: The JavaScript content to parse
    :type js_content: str
    :return: The extracted checkKey value or None if extraction fails
    :rtype: str | None
    """

    # Create temporary file for JavaScript content
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8"
    ) as temp_js:
        temp_js.write(js_content)
        temp_js_path = temp_js.name

    # Create Node.js script for AST extraction
    node_script = f"""
const fs = require('fs');
const acorn = require('acorn');
const walk = require('acorn-walk');

// Load JavaScript
const jsContent = fs.readFileSync('{temp_js_path}', 'utf-8');

// Parse into AST
let ast;
try {{
    ast = acorn.parse(jsContent, {{
        ecmaVersion: 'latest',
        sourceType: 'script'
    }});
}} catch (err) {{
    console.log(JSON.stringify({{
        error: 'Parse failed: ' + err.message
    }}));
    process.exit(1);
}}

// Find all assignments to this.checkKey_ (NO REGEX!)
const assignments = [];

walk.simple(ast, {{
    AssignmentExpression(node) {{
        if (node.left.type === 'MemberExpression' &&
            node.left.object.type === 'ThisExpression' &&
            node.left.property.type === 'Identifier' &&
            node.left.property.name === 'checkKey_') {{

            const start = node.right.start;
            const end = node.right.end;
            const expression = jsContent.substring(start, end);

            assignments.push({{
                position: start,
                expression: expression
            }});
        }}
    }}
}});

// Sort by position (first in file = first in execution)
assignments.sort((a, b) => a.position - b.position);

// Execute each to get values
const values = assignments.map(assignment => {{
    try {{
        const value = eval(assignment.expression);
        return {{
            expression: assignment.expression.substring(0, 100),
            value: value
        }};
    }} catch (err) {{
        return {{
            expression: assignment.expression.substring(0, 100),
            error: err.message
        }};
    }}
}});

console.log(JSON.stringify({{
    success: true,
    assignments_found: values.length,
    values: values,
    checkkey: values.length > 0 ? values[0].value : null
}}));
"""

    temp_script = None
    try:
        # Create temporary Node.js script
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False, encoding="utf-8"
        ) as temp:
            temp.write(node_script)
            temp_script = temp.name

        # Execute Node.js script with absolute path
        node_path = shutil.which("node")
        if not node_path:
            textio_logger.warning(
                "Node.js not found in PATH. Install Node.js and run: npm install acorn acorn-walk"
            )
            return None

        result = subprocess.run(
            [node_path, temp_script],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            output = json.loads(result.stdout.strip())

            if "error" in output:
                textio_logger.warning(f"AST parsing error: {output['error']}")
                return None

            if output.get("checkkey"):
                return output["checkkey"]

        else:
            textio_logger.warning(f"Node.js execution error: {result.stderr}")
            return None

    except FileNotFoundError:
        textio_logger.warning(
            "Node.js not found. Install Node.js and run: npm install acorn acorn-walk"
        )
        textio_logger.warning("Or install JSPyBridge: pip install javascript")
        return None

    except subprocess.TimeoutExpired:
        textio_logger.warning("AST parsing timed out (file too large)")
        return None

    except json.JSONDecodeError as e:
        textio_logger.warning(f"Failed to parse Node.js output: {e}")
        return None

    except Exception as e:
        textio_logger.warning(f"Unexpected error during AST extraction: {e}")
        return None

    finally:
        # Cleanup temporary files
        with suppress(Exception):
            Path(temp_js_path).unlink(missing_ok=True)
            if temp_script:
                Path(temp_script).unlink(missing_ok=True)

    return None


def guess_check_key(user_agent: str) -> str | None:  # noqa: PLR0911
    """Tries to extract the check key from Fansly's main.js using AST parsing.

    This function:
    1. Downloads Fansly homepage to find main.js URL
    2. Downloads main.js
    3. Uses AST parsing to extract checkKey (NO REGEX for finding!)
    4. Falls back to hardcoded default if extraction fails

    Uses JSPyBridge for efficient JavaScript execution, falls back to subprocess.

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

        # Step 2: Download main.js
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

        # Step 3: Extract checkKey using AST parsing (NO REGEX!)
        # Uses JSPyBridge if available, falls back to subprocess
        checkkey = extract_checkkey_from_js(js_response.text)

        if checkkey:
            return checkkey

        # If AST extraction fails, fall back to default
        textio_logger.warning("AST extraction failed, using default checkKey")
        return default_check_key  # noqa: TRY300

    except httpx.RequestError as e:
        textio_logger.error(f"Network error while downloading Fansly files: {e}", 4)
        return default_check_key

    except Exception as e:
        textio_logger.error(f"Unexpected error during checkKey extraction: {e}", 4)
        return default_check_key
