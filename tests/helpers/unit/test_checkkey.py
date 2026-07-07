"""Tests for helpers/checkkey.py — checkKey extraction, validation, nvm setup."""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from helpers.checkkey import (
    _extract_checkkey_ast_fallback,
    _extract_checkkey_regex,
    _extract_expression_at_position,
    _setup_nvm_environment,
    _validate_checkkey_format,
    extract_checkkey_from_js,
    guess_check_key,
)
from tests.fixtures.api import dump_fansly_calls
from tests.fixtures.utils import (
    make_acorn_require,
    make_eval_js,
    make_fake_connection,
    scaled_sync_sleep,
)


# Website-host URLs (scraped for main.js) — no FanslyApi.*_ENDPOINT constant
# fits because these are the public website, not apiv3 endpoints.
FANSLY_HOMEPAGE = "https://fansly.com/"  # CCH:api  # website root for main.js scrape, not an apiv3 endpoint
FANSLY_HOST_BARE = "https://fansly.com"  # CCH:api  # pathless website host literal (bridge tests), not an apiv3 endpoint
FANSLY_MAIN_JS = "https://fansly.com/main.abc123.js"  # CCH:api  # scraped website main.js asset, not an apiv3 endpoint


def _fast_monotonic() -> object:
    """A ``time.monotonic`` stand-in that jumps 100s per call.

    The real AST fallback polls ``time.monotonic`` in its callback-wait
    and queue-drain loops. With a no-op ``time.sleep`` and a real clock
    those loops spin for wall-clock seconds; a clock that leaps forward
    makes every loop hit its timeout/stable condition on the first check,
    keeping the reconciliation tests fast and deterministic.
    """
    clock = [0.0]

    def monotonic() -> float:
        clock[0] += 100.0
        return clock[0]

    return monotonic


# ── _setup_nvm_environment ──────────────────────────────────────────────


class TestSetupNvmEnvironment:
    @pytest.mark.parametrize(
        ("layout", "nvm_env", "clear_node_path"),
        [
            # Line 38: nvm_path doesn't exist → early return.
            pytest.param((), "missing", False, id="no-nvm-dir-early-return"),
            # Lines 46-52, 64→68→73→77→82: real .nvmrc read (Path(__file__)
            # is fixed); resolved version absent from tmp NVM_DIR → line 64.
            pytest.param((".",), "tmp", False, id="nvmrc-read-version-not-installed"),
            # Lines 55-61: versions present but none match the real .nvmrc;
            # no PATH assertion possible (real .nvmrc version won't match).
            pytest.param(
                (
                    "versions/node/v18.0.0/bin",
                    "versions/node/v20.0.0/bin",
                    "versions/node/v20.0.0/lib/node_modules",
                ),
                "tmp",
                True,
                id="versions-present-nvmrc-mismatch",
            ),
            # Lines 56-57: versions/node dir doesn't exist → return.
            pytest.param((".",), "tmp", False, id="no-versions-dir-returns"),
            # Lines 59-60: versions/node dir exists but is empty → return.
            pytest.param(
                ("versions/node",), "tmp", False, id="empty-versions-dir-returns"
            ),
            # Line 64: node_path (.nvmrc version) not installed → return.
            pytest.param((".",), "tmp", False, id="node-path-not-exist-returns"),
            # Line 68: node_path exists but bin/ dir doesn't → return.
            pytest.param(
                ("versions/node/v20.0.0",),
                "tmp",
                False,
                id="node-bin-not-exist-returns",
            ),
            # Lines 81-82: NVM_DIR unset → HOME fallback won't find tmp dir.
            pytest.param(
                ("versions/node/v20.0.0/bin",),
                "unset",
                False,
                id="nvm-dir-unset-home-fallback",
            ),
            # Line 77→81: no lib/node_modules → NODE_PATH not set.
            pytest.param(
                ("versions/node/v20.0.0/bin",),
                "tmp",
                True,
                id="node-modules-missing-skips-node-path",
            ),
        ],
    )
    def test_no_crash_filesystem_layouts(
        self, tmp_path, monkeypatch, layout, nvm_env, clear_node_path
    ):
        """No-crash pokes: build each nvm tree layout, run, expect no error.

        ``layout`` lists dirs to create under the tmp ``.nvm`` root ("." =
        just the root). ``nvm_env`` selects how NVM_DIR is set: "tmp" (the
        built tree), "missing" (points at a nonexistent path), or "unset"
        (deleted so the HOME fallback runs). Rows carry the original
        per-branch line references as comments so a coverage regression
        still names its branch.
        """
        nvm_dir = tmp_path / ".nvm"
        for rel in layout:
            (nvm_dir / rel).mkdir(parents=True, exist_ok=True)
        if nvm_env == "tmp":
            monkeypatch.setenv("NVM_DIR", str(nvm_dir))
        elif nvm_env == "missing":
            monkeypatch.setenv("NVM_DIR", str(tmp_path / "nonexistent"))
        else:
            monkeypatch.delenv("NVM_DIR", raising=False)
        if clear_node_path:
            monkeypatch.delenv("NODE_PATH", raising=False)
        _setup_nvm_environment()

    def test_path_not_duplicated(self, tmp_path, monkeypatch):
        """Line 73: node_bin already in PATH → not prepended again."""
        nvm_dir = tmp_path / ".nvm"
        node_bin = nvm_dir / "versions" / "node" / "v20.0.0" / "bin"
        node_bin.mkdir(parents=True)
        (nvm_dir / "versions" / "node" / "v20.0.0" / "lib" / "node_modules").mkdir(
            parents=True
        )
        monkeypatch.setenv("NVM_DIR", str(nvm_dir))
        monkeypatch.setenv("PATH", f"{node_bin}:/usr/bin")
        _setup_nvm_environment()
        # Should not duplicate
        assert os.environ["PATH"].count(str(node_bin)) == 1

    def test_nvmrc_missing_uses_latest_version(self, tmp_path, monkeypatch):
        """Lines 55-61: nvmrc not readable → fall back to sorted-latest version dir."""
        # Real .nvmrc lives at project root (hardcoded); patch its exists() to deny.
        nvm_dir = tmp_path / ".nvm"
        # Two versions; sorted reverse → v22 wins.
        (nvm_dir / "versions" / "node" / "v18.0.0" / "bin").mkdir(parents=True)
        v_latest = nvm_dir / "versions" / "node" / "v22.0.0" / "bin"
        v_latest.mkdir(parents=True)
        (nvm_dir / "versions" / "node" / "v22.0.0" / "lib" / "node_modules").mkdir(
            parents=True
        )

        monkeypatch.setenv("NVM_DIR", str(nvm_dir))
        original_path = os.environ.get("PATH", "")
        monkeypatch.setenv("PATH", "/usr/bin")

        real_exists = Path.exists

        def selective_exists(self):
            if self.name == ".nvmrc":
                return False
            return real_exists(self)

        monkeypatch.setattr(Path, "exists", selective_exists)
        _setup_nvm_environment()
        # Latest version's bin should be prepended to PATH.
        assert str(v_latest) in os.environ["PATH"]
        # Restore PATH after monkeypatch teardown — keeps other tests isolated.
        monkeypatch.setenv("PATH", original_path)


# ── _extract_expression_at_position ─────────────────────────────────────


class TestExtractExpressionAtPosition:
    def test_simple_value(self):
        js = "x = 42;"
        assert _extract_expression_at_position(js, 4) == "42"

    def test_string_literal(self):
        js = 'x = "hello";'
        assert _extract_expression_at_position(js, 4) == '"hello"'

    def test_nested_parens(self):
        js = "x = (a + b);"
        assert _extract_expression_at_position(js, 4) == "(a + b)"

    def test_nested_brackets(self):
        js = "x = [1, 2, 3];"
        assert _extract_expression_at_position(js, 4) == "[1, 2, 3]"

    def test_nested_braces(self):
        js = "x = {a: 1};"
        assert _extract_expression_at_position(js, 4) == "{a: 1}"

    def test_function_call(self):
        js = 'x = foo("bar");'
        assert _extract_expression_at_position(js, 4) == 'foo("bar")'

    def test_newline_terminates(self):
        js = "x = 42\ny = 10"
        assert _extract_expression_at_position(js, 4) == "42"

    def test_comma_terminates_at_depth_0(self):
        js = "x = 42, y = 10"
        assert _extract_expression_at_position(js, 4) == "42"

    def test_comma_inside_parens_not_terminate(self):
        js = "x = foo(1, 2);"
        assert _extract_expression_at_position(js, 4) == "foo(1, 2)"

    def test_closing_bracket_at_negative_depth(self):
        js = "x = 42)"
        assert _extract_expression_at_position(js, 4) == "42"

    def test_string_with_escaped_quote(self):
        js = r"""x = "he\"llo";"""
        result = _extract_expression_at_position(js, 4)
        assert result is not None
        assert "he" in result

    def test_template_literal(self):
        js = "x = `hello`;"
        assert _extract_expression_at_position(js, 4) == "`hello`"

    def test_string_with_semicolon_inside(self):
        js = 'x = "a;b";'
        assert _extract_expression_at_position(js, 4) == '"a;b"'

    def test_leading_whitespace_skipped(self):
        js = "x =   42;"
        assert _extract_expression_at_position(js, 4) == "42"

    def test_empty_after_whitespace(self):
        """Line 120: pos >= length after skipping whitespace → None."""
        js = "x =   "
        assert _extract_expression_at_position(js, 4) is None

    def test_nothing_extracted(self):
        """Line 178: pos == start (empty expression) → None."""
        js = "x = ;"
        assert _extract_expression_at_position(js, 4) is None

    def test_complex_expression(self):
        """Realistic: array reverse + join — Fansly's actual pattern."""
        js = 'this.checkKey_ = ["fySzis","oybZy8"].reverse().join("-")+"-bubayf";'
        result = _extract_expression_at_position(js, 17)
        assert result is not None
        assert "reverse" in result
        assert "join" in result


# ── _validate_checkkey_format ───────────────────────────────────────────


class TestValidateCheckkeyFormat:
    def test_valid_format(self):
        assert _validate_checkkey_format("oybZy8-fySzis-bubayf") is True

    def test_empty_string(self):
        assert _validate_checkkey_format("") is False

    def test_not_string(self):
        # Deliberate invalid-input: exercises the `isinstance(checkkey, str)`
        # guard in production; no in-contract (str) value can be non-str.
        assert _validate_checkkey_format(12345) is False  # type: ignore[arg-type]
        assert _validate_checkkey_format(None) is False  # type: ignore[arg-type]

    def test_no_hyphens(self):
        assert _validate_checkkey_format("abcdefghijk") is False

    def test_too_short(self):
        assert _validate_checkkey_format("ab-cd") is False

    def test_too_long(self):
        assert _validate_checkkey_format("a-" * 30) is False

    def test_special_chars_rejected(self):
        assert _validate_checkkey_format("abc-def!-ghi") is False
        assert _validate_checkkey_format("abc def-ghi") is False

    def test_alphanumeric_with_hyphens(self):
        assert _validate_checkkey_format("abc123-def456-ghi") is True


# ── _extract_checkkey_regex ─────────────────────────────────────────────


class TestExtractCheckkeyRegex:
    def test_valid_assignment(self):
        """Lines 228-290: finds this.checkKey_ assignment, eval_js returns value."""
        js = 'this.checkKey_ = ["fySzis","oybZy8"].reverse().join("-")+"-bubayf";'
        with patch("helpers.checkkey.eval_js", return_value="oybZy8-fySzis-bubayf"):
            result = _extract_checkkey_regex(js)
        assert result == "oybZy8-fySzis-bubayf"

    def test_no_assignments_found(self):
        """Lines 235-237: no this.checkKey_ in content → None."""
        result = _extract_checkkey_regex("var x = 42;")
        assert result is None

    def test_expression_extraction_fails(self):
        """Lines 250-254, 263-265: expression can't be extracted → None."""
        js = "this.checkKey_ = ;"
        result = _extract_checkkey_regex(js)
        assert result is None

    def test_validation_fails(self):
        """Lines 278-282: eval_js returns invalid format → None."""
        js = 'this.checkKey_ = "bad";'
        with patch("helpers.checkkey.eval_js", return_value="bad"):
            result = _extract_checkkey_regex(js)
        assert result is None

    def test_eval_js_exception(self):
        """Lines 286-288: eval_js raises → caught, returns None."""
        js = "this.checkKey_ = something_complex();"
        with patch("helpers.checkkey.eval_js", side_effect=RuntimeError("js error")):
            result = _extract_checkkey_regex(js)
        assert result is None

    def test_multiple_assignments_uses_first(self):
        """Lines 268-269: multiple assignments → uses first (sorted by position)."""
        js = (
            'this.checkKey_ = ["fySzis","oybZy8"].reverse().join("-")+"-bubayf";\n'
            'this.checkKey_ = "decoy-value-here";'
        )
        with patch("helpers.checkkey.eval_js", return_value="oybZy8-fySzis-bubayf"):
            result = _extract_checkkey_regex(js)
        assert result == "oybZy8-fySzis-bubayf"


# ── extract_checkkey_from_js ────────────────────────────────────────────


def _build_reconciliation_js(
    regex_expr: str = "REGEXEXPR",
    ast_expr: str = "ASTEXPR",
) -> tuple[str, list[tuple[int, int]]]:
    """Build real JS + the AST span that drives the reconciliation path.

    The regex extractor matches ``this.checkKey_ = <regex_expr>`` and slices
    ``regex_expr``. The AST fallback is told (via ``make_acorn_require``) to
    "discover" an assignment whose source span points at ``ast_expr``, so
    the regex and AST paths slice DIFFERENT expressions. A single
    ``eval_js`` map then returns whatever value we want for each, letting
    real ``extract_checkkey_from_js`` reconciliation logic run end-to-end.
    """
    js = f"this.checkKey_ = {regex_expr};\nvar astMarker = {ast_expr};"
    ast_start = js.index(ast_expr)
    ast_spans = [(ast_start, ast_start + len(ast_expr))]
    return js, ast_spans


class TestExtractCheckkeyFromJs:
    """Real regex-vs-AST reconciliation over real JS; only the JSPyBridge
    leaves (``eval_js``, ``require``/acorn) are patched.

    Replaces nine tests that stubbed ``_extract_checkkey_regex`` and
    ``_extract_checkkey_ast_fallback`` directly — which bypassed the real
    regex extractor, the expression slicer, and the reconciliation
    branches the function exists to exercise. Here the real internals run;
    only the Node-subprocess boundary is faked.

    FINDING (dead defensive branches, not a bug): two of the nine old
    stubbed tests asserted ``regex_checkkey`` was *truthy but invalid
    format* — a state ``_extract_checkkey_regex`` can never return, since
    it runs ``_validate_checkkey_format`` itself and returns None on
    failure (checkkey.py lines 279-283). So the "regex failed validation"
    arms in ``extract_checkkey_from_js`` (the ``return expected_checkkey``
    fallback and the no-expected fall-through-to-AST) are unreachable via
    the real path; only the stubs feeding impossible inputs ever hit them.
    They're now marked ``# pragma: no cover`` with the invariant
    documented in place (Option A: surface + annotate, no papering over).
    """

    @pytest.mark.parametrize(
        ("regex_value", "ast_value", "expected_checkkey", "result"),
        [
            # Regex matches expected → fast path, AST never runs (lines 322-326).
            pytest.param(
                "oybZy8-fySzis-bubayf",
                None,
                "oybZy8-fySzis-bubayf",
                "oybZy8-fySzis-bubayf",
                id="regex-matches-expected",
            ),
            # Regex != expected, AST == regex → AST confirms regex (lines 336-342).
            pytest.param(
                "new-key-value1",
                "new-key-value1",
                "old-key-value1",
                "new-key-value1",
                id="regex-mismatch-ast-confirms-regex",
            ),
            # Regex != expected, AST == expected → AST confirms expected (lines 343-348).
            pytest.param(
                "wrong-key-val1",
                "old-key-value1",
                "old-key-value1",
                "old-key-value1",
                id="regex-mismatch-ast-confirms-expected",
            ),
            # All three differ → trust AST as authoritative (lines 350-356).
            pytest.param(
                "regex-key-val1",
                "third-key-val1",
                "expected-keyv1",
                "third-key-val1",
                id="regex-mismatch-ast-third-value",
            ),
            # AST fails (None), regex still valid → trust regex (lines 358-364).
            pytest.param(
                "valid-key-value",
                None,
                "different-keyv1",
                "valid-key-value",
                id="regex-mismatch-ast-fails-regex-valid",
            ),
            # Regex succeeds, no expected → validate + return regex (lines 372-375).
            pytest.param(
                "good-regex-keyv1",
                None,
                None,
                "good-regex-keyv1",
                id="regex-success-no-expected",
            ),
        ],
    )
    def test_reconciliation_branches(
        self, regex_value, ast_value, expected_checkkey, result
    ):
        """Drive each reconciliation branch with real JS + leaf-only patches.

        ``regex_value`` is what ``eval_js`` returns for the regex-matched
        expression; ``ast_value`` is what it returns for the AST-discovered
        expression (or ``None`` to make the AST fallback yield nothing —
        either by mapping the AST expr to ``None`` so validation rejects
        it, no: we simulate AST failure by having acorn find no assignment).
        """
        js, ast_spans = _build_reconciliation_js()
        eval_map = {"REGEXEXPR": regex_value}

        if ast_value is None:
            # AST fallback finds NO this.checkKey_ assignment → returns None,
            # exercising the real "AST failed" reconciliation arms.
            fake_require = make_acorn_require(spans=[])
        else:
            eval_map["ASTEXPR"] = ast_value
            fake_require = make_acorn_require(spans=ast_spans)

        with (
            patch("helpers.checkkey.eval_js", make_eval_js(eval_map)),
            patch("helpers.checkkey.require", side_effect=fake_require),
            patch("helpers.checkkey.connection", make_fake_connection()),
            patch("helpers.checkkey.globalThis", MagicMock()),
            patch("helpers.checkkey.time.sleep", scaled_sync_sleep),
            patch("helpers.checkkey.time.monotonic", side_effect=_fast_monotonic()),
        ):
            extracted = extract_checkkey_from_js(
                js, expected_checkkey=expected_checkkey
            )

        assert extracted == result

    def test_regex_fails_falls_to_ast(self):
        """Regex finds no assignment → real AST fallback supplies the key.

        No ``this.checkKey_`` text means the real regex extractor returns
        None (no internal stub), so production falls through to the AST
        path, which the acorn leaf drives to discover the assignment.
        """
        js = "var astMarker = ASTEXPR;"
        ast_start = js.index("ASTEXPR")
        spans = [(ast_start, ast_start + len("ASTEXPR"))]

        with (
            patch(
                "helpers.checkkey.eval_js", make_eval_js({"ASTEXPR": "ast-key-value1"})
            ),
            patch(
                "helpers.checkkey.require", side_effect=make_acorn_require(spans=spans)
            ),
            patch("helpers.checkkey.connection", make_fake_connection()),
            patch("helpers.checkkey.globalThis", MagicMock()),
            patch("helpers.checkkey.time.sleep", scaled_sync_sleep),
            patch("helpers.checkkey.time.monotonic", side_effect=_fast_monotonic()),
        ):
            result = extract_checkkey_from_js(js)

        assert result == "ast-key-value1"


# ── _extract_checkkey_ast_fallback ──────────────────────────────────────


class TestExtractCheckkeyAstFallback:
    """Mock JSPyBridge boundary: require, eval_js, connection, globalThis."""

    def _make_mock_acorn(self, js_content, assignments_data):
        """Build mock acorn/acorn_walk that simulates AST walking."""
        mock_ast = MagicMock(name="ast")
        mock_acorn = MagicMock(name="acorn")
        mock_acorn.parse.return_value = mock_ast
        mock_walk = MagicMock(name="acorn_walk")

        def simulate_walk(ast, callbacks):
            """Simulate acorn_walk.simple by calling the callback."""
            cb = callbacks.get("AssignmentExpression")
            if cb:
                for data in assignments_data:
                    # Build a fake AST node
                    node = SimpleNamespace(
                        type="AssignmentExpression",
                        left=SimpleNamespace(
                            type="MemberExpression",
                            object=SimpleNamespace(type="ThisExpression"),
                            property=SimpleNamespace(
                                type="Identifier", name="checkKey_"
                            ),
                        ),
                        right=SimpleNamespace(start=data["start"], end=data["end"]),
                    )
                    cb(node, None)

        mock_walk.simple.side_effect = simulate_walk
        return mock_acorn, mock_walk

    def test_successful_extraction(self):
        """Lines 405-599: full happy path — parse, walk, extract, eval, cleanup."""
        js = 'prefix this.checkKey_ = ["a","b"].reverse().join("-")+"-c"; suffix'
        expr_start = 27
        expr_end = 62

        mock_acorn, mock_walk = self._make_mock_acorn(
            js, [{"start": expr_start, "end": expr_end}]
        )
        mock_conn = MagicMock()
        mock_conn.sendQ = []
        mock_conn.com_items = []

        def mock_require(module):
            if module == "acorn":
                return mock_acorn
            if module == "acorn-walk":
                return mock_walk
            return MagicMock()

        # Advance time so polling loops complete quickly
        call_count = [0]

        def fast_monotonic():
            call_count[0] += 1
            return (
                call_count[0] * 0.6
            )  # Each call advances 0.6s → stable after 3 checks

        with (
            patch("helpers.checkkey.require", side_effect=mock_require),
            patch("helpers.checkkey.eval_js", return_value="b-a-c"),
            patch("helpers.checkkey.connection", mock_conn),
            patch("helpers.checkkey.globalThis", MagicMock()),
            patch("helpers.checkkey.time.monotonic", side_effect=fast_monotonic),
            patch("helpers.checkkey.time.sleep", scaled_sync_sleep),
        ):
            result = _extract_checkkey_ast_fallback(js)

        assert result == "b-a-c"

    def test_no_assignments_found(self):
        """Lines 541-552: walk finds no checkKey_ assignments → None."""
        mock_acorn, mock_walk = self._make_mock_acorn("js", [])
        mock_conn = MagicMock()
        mock_conn.sendQ = []
        mock_conn.com_items = []

        def mock_require(module):
            if module == "acorn":
                return mock_acorn
            if module == "acorn-walk":
                return mock_walk
            return MagicMock()

        # Fast-forward time so polling loops exit immediately
        clock = [0.0]

        def fast_monotonic():
            clock[0] += 100.0  # Jump 100s per call → instant timeout
            return clock[0]

        with (
            patch("helpers.checkkey.require", side_effect=mock_require),
            patch("helpers.checkkey.connection", mock_conn),
            patch("helpers.checkkey.globalThis", MagicMock()),
            patch("helpers.checkkey.time.monotonic", side_effect=fast_monotonic),
            patch("helpers.checkkey.time.sleep", scaled_sync_sleep),
        ):
            result = _extract_checkkey_ast_fallback("var x = 1;")

        assert result is None

    def test_non_matching_nodes_skipped(self):
        """Lines 434→exit: AST nodes that don't match checkKey_ pattern are skipped."""
        mock_ast = MagicMock()
        mock_acorn = MagicMock()
        mock_acorn.parse.return_value = mock_ast
        mock_walk = MagicMock()

        def simulate_walk_with_mismatches(ast, callbacks):
            cb = callbacks.get("AssignmentExpression")
            if cb:
                # Node with wrong type string
                cb(SimpleNamespace(type="VariableDeclaration"), None)
                # AssignmentExpression but left is not MemberExpression
                cb(
                    SimpleNamespace(
                        type="AssignmentExpression",
                        left=SimpleNamespace(type="Identifier"),
                    ),
                    None,
                )
                # MemberExpression but object is not ThisExpression
                cb(
                    SimpleNamespace(
                        type="AssignmentExpression",
                        left=SimpleNamespace(
                            type="MemberExpression",
                            object=SimpleNamespace(type="Identifier"),
                            property=SimpleNamespace(
                                type="Identifier", name="checkKey_"
                            ),
                        ),
                    ),
                    None,
                )
                # ThisExpression but property is not Identifier
                cb(
                    SimpleNamespace(
                        type="AssignmentExpression",
                        left=SimpleNamespace(
                            type="MemberExpression",
                            object=SimpleNamespace(type="ThisExpression"),
                            property=SimpleNamespace(type="Literal", name="checkKey_"),
                        ),
                    ),
                    None,
                )
                # Identifier but name is not checkKey_
                cb(
                    SimpleNamespace(
                        type="AssignmentExpression",
                        left=SimpleNamespace(
                            type="MemberExpression",
                            object=SimpleNamespace(type="ThisExpression"),
                            property=SimpleNamespace(
                                type="Identifier", name="otherKey"
                            ),
                        ),
                    ),
                    None,
                )

        mock_walk.simple.side_effect = simulate_walk_with_mismatches

        def mock_require(module):
            if module == "acorn":
                return mock_acorn
            if module == "acorn-walk":
                return mock_walk
            return MagicMock()

        mock_conn = MagicMock()
        mock_conn.sendQ = []
        mock_conn.com_items = []

        clock = [0.0]

        def fast_monotonic():
            clock[0] += 100.0
            return clock[0]

        with (
            patch("helpers.checkkey.require", side_effect=mock_require),
            patch("helpers.checkkey.connection", mock_conn),
            patch("helpers.checkkey.globalThis", MagicMock()),
            patch("helpers.checkkey.time.monotonic", side_effect=fast_monotonic),
            patch("helpers.checkkey.time.sleep", scaled_sync_sleep),
        ):
            result = _extract_checkkey_ast_fallback("var x = 1;")

        assert result is None  # No matching nodes → no assignments

    def test_drain_timeout(self):
        """Line 530: queue drain times out (sendQ never empties)."""
        js = 'prefix this.checkKey_ = "val"; suffix'
        mock_acorn = MagicMock()
        mock_acorn.parse.return_value = MagicMock()
        mock_walk = MagicMock()

        def simulate_walk(ast, callbacks):
            cb = callbacks.get("AssignmentExpression")
            if cb:
                node = SimpleNamespace(
                    type="AssignmentExpression",
                    left=SimpleNamespace(
                        type="MemberExpression",
                        object=SimpleNamespace(type="ThisExpression"),
                        property=SimpleNamespace(type="Identifier", name="checkKey_"),
                    ),
                    right=SimpleNamespace(start=27, end=32),
                )
                cb(node, None)

        mock_walk.simple.side_effect = simulate_walk
        mock_conn = MagicMock()
        mock_conn.sendQ = ["pending"]  # Never empties → drain timeout
        mock_conn.com_items = []

        def mock_require(module):
            if module == "acorn":
                return mock_acorn
            if module == "acorn-walk":
                return mock_walk
            return MagicMock()

        call_count = [0]

        def fast_monotonic():
            call_count[0] += 1
            return call_count[0] * 0.6

        with (
            patch("helpers.checkkey.require", side_effect=mock_require),
            patch("helpers.checkkey.eval_js", return_value="val"),
            patch("helpers.checkkey.connection", mock_conn),
            patch("helpers.checkkey.globalThis", MagicMock()),
            patch("helpers.checkkey.time.monotonic", side_effect=fast_monotonic),
            patch("helpers.checkkey.time.sleep", scaled_sync_sleep),
        ):
            result = _extract_checkkey_ast_fallback(js)

        assert result == "val"  # Still returns despite drain timeout

    def test_exception_during_parse(self):
        """Lines 601-609: acorn.parse throws → caught, returns None."""
        mock_acorn = MagicMock()
        mock_acorn.parse.side_effect = RuntimeError("parse error")
        mock_walk = MagicMock()

        def mock_require(module):
            if module == "acorn":
                return mock_acorn
            if module == "acorn-walk":
                return mock_walk
            return MagicMock()

        with patch("helpers.checkkey.require", side_effect=mock_require):
            result = _extract_checkkey_ast_fallback("invalid js {{{")

        assert result is None


# ── guess_check_key ─────────────────────────────────────────────────────


@patch(
    "helpers.checkkey._shutdown_js_bridge"
)  # ~7s of thread joins skew coverage; real teardown lives in TestJsBridgeShutdown
class TestGuessCheckKey:
    """HTTP boundary: respx_fansly_api. JS boundary: patch extract_checkkey_from_js."""

    def test_success_full_flow(self, mock_shutdown, respx_fansly_api):
        """Homepage → find main.js → download → extract → return."""
        html = '<script src="main.abc123.js"></script>'
        js_content = 'this.checkKey_ = "test";'

        homepage_route = respx.get(FANSLY_HOMEPAGE).mock(
            side_effect=[httpx.Response(200, text=html)]
        )
        mainjs_route = respx.get(FANSLY_MAIN_JS).mock(
            side_effect=[httpx.Response(200, text=js_content)]
        )
        try:
            with patch(
                "helpers.checkkey.extract_checkkey_from_js",
                return_value="oybZy8-fySzis-bubayf",
            ):
                result = guess_check_key("Mozilla/5.0")
        finally:
            dump_fansly_calls(respx.calls, label="test_success_full_flow")

        assert result == "oybZy8-fySzis-bubayf"
        assert homepage_route.called
        assert mainjs_route.called

    def test_homepage_non_200(self, mock_shutdown, respx_fansly_api):
        """Homepage returns non-200 → default key."""
        homepage_route = respx.get(FANSLY_HOMEPAGE).mock(
            side_effect=[httpx.Response(503, text="down")]
        )
        try:
            result = guess_check_key("Mozilla/5.0")
        finally:
            dump_fansly_calls(respx.calls, label="test_homepage_non_200")

        assert result == "oybZy8-fySzis-bubayf"
        assert homepage_route.called

    def test_no_main_js_in_html(self, mock_shutdown, respx_fansly_api):
        """main.js URL not found in HTML → default key."""
        homepage_route = respx.get(FANSLY_HOMEPAGE).mock(
            side_effect=[httpx.Response(200, text="<html>no scripts</html>")]
        )
        try:
            result = guess_check_key("Mozilla/5.0")
        finally:
            dump_fansly_calls(respx.calls, label="test_no_main_js_in_html")

        assert result == "oybZy8-fySzis-bubayf"
        assert homepage_route.called

    def test_main_js_non_200(self, mock_shutdown, respx_fansly_api):
        """Lines 784-788: main.js download fails → default key."""
        html = '<script src="main.abc123.js"></script>'
        homepage_route = respx.get(FANSLY_HOMEPAGE).mock(
            side_effect=[httpx.Response(200, text=html)]
        )
        mainjs_route = respx.get(FANSLY_MAIN_JS).mock(
            side_effect=[httpx.Response(404, text="not found")]
        )
        try:
            result = guess_check_key("Mozilla/5.0")
        finally:
            dump_fansly_calls(respx.calls, label="test_main_js_non_200")

        assert result == "oybZy8-fySzis-bubayf"
        assert homepage_route.called
        assert mainjs_route.called

    def test_extraction_returns_none(self, mock_shutdown, respx_fansly_api):
        """Lines 800-807: extraction returns None → fall back to default."""
        html = '<script src="main.abc123.js"></script>'
        homepage_route = respx.get(FANSLY_HOMEPAGE).mock(
            side_effect=[httpx.Response(200, text=html)]
        )
        mainjs_route = respx.get(FANSLY_MAIN_JS).mock(
            side_effect=[httpx.Response(200, text="var x;")]
        )
        try:
            with patch(
                "helpers.checkkey.extract_checkkey_from_js", return_value=None
            ) as extract_mock:
                result = guess_check_key("Mozilla/5.0")
        finally:
            dump_fansly_calls(respx.calls, label="test_extraction_returns_none")

        assert result == "oybZy8-fySzis-bubayf"
        assert homepage_route.called
        assert mainjs_route.called
        assert extract_mock.called

    def test_network_error(self, mock_shutdown, respx_fansly_api):
        """httpx.RequestError → default key."""
        homepage_route = respx.get(FANSLY_HOMEPAGE).mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        try:
            result = guess_check_key("Mozilla/5.0")
        finally:
            dump_fansly_calls(respx.calls, label="test_network_error")

        assert result == "oybZy8-fySzis-bubayf"
        assert homepage_route.called

    def test_unexpected_exception(self, mock_shutdown, respx_fansly_api):
        """Unexpected error → default key."""
        homepage_route = respx.get(FANSLY_HOMEPAGE).mock(
            side_effect=RuntimeError("boom")
        )
        try:
            result = guess_check_key("Mozilla/5.0")
        finally:
            dump_fansly_calls(respx.calls, label="test_unexpected_exception")

        assert result == "oybZy8-fySzis-bubayf"
        assert homepage_route.called


# ── JS bridge shutdown ─────────────────────────────────────────────────


class TestJsBridgeShutdown:
    """Verify the node bridge subprocess is terminated after checkKey extraction.

    JSPyBridge spawns a Node.js child process on first import; that child
    would otherwise linger for the entire daemon run (hours). The finally
    block in guess_check_key must call connection.stop() so the bridge
    dies at the natural end of its useful lifetime.
    """

    def test_bridge_stopped_on_success(self):
        """connection.stop is called when extraction succeeds."""
        html = '<script src="main.abc123.js"></script>'
        js_content = 'this.checkKey_ = "test";'

        with (
            respx.mock,
            patch("helpers.checkkey.connection") as mock_connection,
            patch(
                "helpers.checkkey.extract_checkkey_from_js",
                return_value="oybZy8-fySzis-bubayf",
            ),
        ):
            homepage_route = respx.get(FANSLY_HOST_BARE).mock(
                side_effect=[httpx.Response(200, text=html)]
            )
            mainjs_route = respx.get(FANSLY_MAIN_JS).mock(
                side_effect=[httpx.Response(200, text=js_content)]
            )
            try:
                guess_check_key("Mozilla/5.0")
            finally:
                dump_fansly_calls(homepage_route.calls, "bridge-success-homepage")
                dump_fansly_calls(mainjs_route.calls, "bridge-success-mainjs")

        mock_connection.stop.assert_called_once()

    def test_bridge_stopped_on_network_error(self):
        """connection.stop is called even when network fails before JS use."""
        with (
            respx.mock,
            patch("helpers.checkkey.connection") as mock_connection,
        ):
            homepage_route = respx.get(FANSLY_HOST_BARE).mock(
                side_effect=httpx.ConnectError("boom")
            )
            try:
                guess_check_key("Mozilla/5.0")
            finally:
                dump_fansly_calls(homepage_route.calls, "bridge-network-error")

        mock_connection.stop.assert_called_once()

    def test_bridge_stopped_on_unexpected_exception(self):
        """connection.stop runs from the finally block even when an unexpected error raises."""
        with (
            respx.mock,
            patch("helpers.checkkey.connection") as mock_connection,
        ):
            homepage_route = respx.get(FANSLY_HOST_BARE).mock(
                side_effect=RuntimeError("boom")
            )
            try:
                guess_check_key("Mozilla/5.0")
            finally:
                dump_fansly_calls(homepage_route.calls, "bridge-unexpected-exc")

        mock_connection.stop.assert_called_once()

    def test_bridge_stop_exception_is_suppressed(self):
        """If connection.stop raises, we don't propagate — the checkKey result stands."""
        html = '<script src="main.abc123.js"></script>'
        js_content = 'this.checkKey_ = "test";'

        with (
            respx.mock,
            patch("helpers.checkkey.connection") as mock_connection,
            patch(
                "helpers.checkkey.extract_checkkey_from_js",
                return_value="oybZy8-fySzis-bubayf",
            ),
        ):
            mock_connection.stop.side_effect = RuntimeError("bridge already stopped")
            homepage_route = respx.get(FANSLY_HOST_BARE).mock(
                side_effect=[httpx.Response(200, text=html)]
            )
            mainjs_route = respx.get(FANSLY_MAIN_JS).mock(
                side_effect=[httpx.Response(200, text=js_content)]
            )
            try:
                # Should NOT raise despite connection.stop throwing
                result = guess_check_key("Mozilla/5.0")
            finally:
                dump_fansly_calls(homepage_route.calls, "bridge-stop-exc-homepage")
                dump_fansly_calls(mainjs_route.calls, "bridge-stop-exc-mainjs")

        assert result == "oybZy8-fySzis-bubayf"
        mock_connection.stop.assert_called_once()
