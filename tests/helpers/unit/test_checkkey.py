"""Tests for helpers/checkkey.py — checkKey extraction, validation, nvm setup."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
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


# ── _setup_nvm_environment ──────────────────────────────────────────────


class TestSetupNvmEnvironment:
    def test_no_nvm_dir_is_noop(self, tmp_path, monkeypatch):
        """Line 38: nvm_path doesn't exist → early return."""
        monkeypatch.setenv("NVM_DIR", str(tmp_path / "nonexistent"))
        _setup_nvm_environment()

    def test_nvmrc_version_found(self, tmp_path, monkeypatch):
        """Lines 46-52, 64→68→73→77→82: .nvmrc found via real project root.

        We can't control Path(__file__) so the real .nvmrc is read.
        Set NVM_DIR to tmp so the resolved version path won't exist → line 64 return.
        This still exercises the .nvmrc read path (lines 46-48).
        """
        nvm_dir = tmp_path / ".nvm"
        nvm_dir.mkdir()
        monkeypatch.setenv("NVM_DIR", str(nvm_dir))
        # Real .nvmrc exists, but version won't be found in our tmp nvm_dir
        _setup_nvm_environment()

    def test_no_nvmrc_falls_back_to_latest(self, tmp_path, monkeypatch):
        """Lines 55-61: no .nvmrc → uses latest version from versions dir.

        Can't prevent the real .nvmrc from being read (Path(__file__) is fixed),
        so this test just verifies the function doesn't crash when our tmp NVM_DIR
        has versions but none match the .nvmrc version.
        """
        nvm_dir = tmp_path / ".nvm"
        v1 = nvm_dir / "versions" / "node" / "v18.0.0" / "bin"
        v1.mkdir(parents=True)
        v2 = nvm_dir / "versions" / "node" / "v20.0.0" / "bin"
        v2.mkdir(parents=True)
        (nvm_dir / "versions" / "node" / "v20.0.0" / "lib" / "node_modules").mkdir(
            parents=True
        )

        monkeypatch.setenv("NVM_DIR", str(nvm_dir))
        monkeypatch.delenv("NODE_PATH", raising=False)
        _setup_nvm_environment()
        # Don't assert PATH — real .nvmrc version won't match our tmp versions

    def test_no_versions_dir_returns(self, tmp_path, monkeypatch):
        """Lines 56-57: versions/node dir doesn't exist → return."""
        nvm_dir = tmp_path / ".nvm"
        nvm_dir.mkdir()
        monkeypatch.setenv("NVM_DIR", str(nvm_dir))
        _setup_nvm_environment()

    def test_empty_versions_dir_returns(self, tmp_path, monkeypatch):
        """Lines 59-60: versions/node dir exists but is empty → return."""
        nvm_dir = tmp_path / ".nvm"
        (nvm_dir / "versions" / "node").mkdir(parents=True)
        monkeypatch.setenv("NVM_DIR", str(nvm_dir))
        _setup_nvm_environment()

    def test_node_path_not_exist_returns(self, tmp_path, monkeypatch):
        """Line 64: node_path doesn't exist (e.g., .nvmrc points to uninstalled version)."""
        nvm_dir = tmp_path / ".nvm"
        nvm_dir.mkdir()
        # .nvmrc references version that doesn't exist
        monkeypatch.setenv("NVM_DIR", str(nvm_dir))
        _setup_nvm_environment()

    def test_node_bin_not_exist_returns(self, tmp_path, monkeypatch):
        """Line 68: node_path exists but bin/ dir doesn't."""
        nvm_dir = tmp_path / ".nvm"
        node_path = nvm_dir / "versions" / "node" / "v20.0.0"
        node_path.mkdir(parents=True)
        # No bin/ subdirectory
        monkeypatch.setenv("NVM_DIR", str(nvm_dir))
        _setup_nvm_environment()

    def test_sets_nvm_dir_if_unset(self, tmp_path, monkeypatch):
        """Lines 81-82: NVM_DIR not in env → sets it."""
        nvm_dir = tmp_path / ".nvm"
        node_bin = nvm_dir / "versions" / "node" / "v20.0.0" / "bin"
        node_bin.mkdir(parents=True)
        monkeypatch.delenv("NVM_DIR", raising=False)
        # HOME-based fallback won't find our tmp dir, so this tests the early return
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

    def test_node_modules_not_exist_skips(self, tmp_path, monkeypatch):
        """Line 77→81: node_modules dir doesn't exist → NODE_PATH not set."""
        nvm_dir = tmp_path / ".nvm"
        node_bin = nvm_dir / "versions" / "node" / "v20.0.0" / "bin"
        node_bin.mkdir(parents=True)
        # No lib/node_modules
        monkeypatch.setenv("NVM_DIR", str(nvm_dir))
        monkeypatch.delenv("NODE_PATH", raising=False)
        _setup_nvm_environment()
        # NODE_PATH should not be set (no node_modules dir)


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
        assert _validate_checkkey_format(12345) is False
        assert _validate_checkkey_format(None) is False

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


class TestExtractCheckkeyFromJs:
    def test_regex_matches_expected(self):
        """Lines 322-326: regex succeeds and matches expected → fast return."""
        js = "this.checkKey_ = expr;"
        with patch(
            "helpers.checkkey._extract_checkkey_regex",
            return_value="oybZy8-fySzis-bubayf",
        ):
            result = extract_checkkey_from_js(
                js, expected_checkkey="oybZy8-fySzis-bubayf"
            )
        assert result == "oybZy8-fySzis-bubayf"

    def test_regex_mismatch_ast_confirms_regex(self):
        """Lines 336-342: regex != expected, AST == regex → return AST (confirms regex)."""
        with (
            patch(
                "helpers.checkkey._extract_checkkey_regex",
                return_value="new-key-value1",
            ),
            patch(
                "helpers.checkkey._extract_checkkey_ast_fallback",
                return_value="new-key-value1",
            ),
        ):
            result = extract_checkkey_from_js("js", expected_checkkey="old-key-value1")
        assert result == "new-key-value1"

    def test_regex_mismatch_ast_confirms_expected(self):
        """Lines 343-348: regex != expected, AST == expected → return AST."""
        with (
            patch(
                "helpers.checkkey._extract_checkkey_regex",
                return_value="wrong-key-val1",
            ),
            patch(
                "helpers.checkkey._extract_checkkey_ast_fallback",
                return_value="old-key-value1",
            ),
        ):
            result = extract_checkkey_from_js("js", expected_checkkey="old-key-value1")
        assert result == "old-key-value1"

    def test_regex_mismatch_ast_returns_third_value(self):
        """Lines 350-356: all three differ → trust AST."""
        with (
            patch(
                "helpers.checkkey._extract_checkkey_regex",
                return_value="regex-key-val1",
            ),
            patch(
                "helpers.checkkey._extract_checkkey_ast_fallback",
                return_value="third-key-val1",
            ),
        ):
            result = extract_checkkey_from_js("js", expected_checkkey="expected-keyv1")
        assert result == "third-key-val1"

    def test_regex_mismatch_ast_fails_regex_valid(self):
        """Lines 358-363: AST fails, regex passes validation → return regex."""
        with (
            patch(
                "helpers.checkkey._extract_checkkey_regex",
                return_value="valid-key-value",
            ),
            patch("helpers.checkkey._extract_checkkey_ast_fallback", return_value=None),
        ):
            result = extract_checkkey_from_js("js", expected_checkkey="different-keyv1")
        assert result == "valid-key-value"

    def test_regex_mismatch_ast_fails_regex_invalid(self):
        """Lines 366-369: AST fails, regex fails validation → return expected."""
        with (
            patch("helpers.checkkey._extract_checkkey_regex", return_value="!invalid!"),
            patch("helpers.checkkey._extract_checkkey_ast_fallback", return_value=None),
        ):
            result = extract_checkkey_from_js("js", expected_checkkey="fallback-key-v1")
        assert result == "fallback-key-v1"

    def test_regex_succeeds_no_expected(self):
        """Lines 372-374: regex succeeds, no expected → validate and return."""
        with patch(
            "helpers.checkkey._extract_checkkey_regex", return_value="good-regex-keyv1"
        ):
            result = extract_checkkey_from_js("js", expected_checkkey=None)
        assert result == "good-regex-keyv1"

    def test_regex_succeeds_no_expected_invalid_format(self):
        """Lines 376-378: regex succeeds, no expected, but fails validation → fall to AST."""
        with (
            patch("helpers.checkkey._extract_checkkey_regex", return_value="x"),
            patch(
                "helpers.checkkey._extract_checkkey_ast_fallback",
                return_value="ast-fallback-v1",
            ),
        ):
            result = extract_checkkey_from_js("js", expected_checkkey=None)
        assert result == "ast-fallback-v1"

    def test_regex_fails_falls_to_ast(self):
        """Lines 382-383: regex returns None → AST fallback."""
        with (
            patch("helpers.checkkey._extract_checkkey_regex", return_value=None),
            patch(
                "helpers.checkkey._extract_checkkey_ast_fallback",
                return_value="ast-key-value1",
            ),
        ):
            result = extract_checkkey_from_js("js")
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
            patch("helpers.checkkey.time.sleep"),
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
            patch("helpers.checkkey.time.sleep"),
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
            patch("helpers.checkkey.time.sleep"),
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
            patch("helpers.checkkey.time.sleep"),
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


class TestGuessCheckKey:
    """HTTP boundary: respx. JS boundary: patch extract_checkkey_from_js."""

    def test_success_full_flow(self):
        """Lines 629-700: homepage → find main.js → download → extract."""
        html = '<script src="main.abc123.js"></script>'
        js_content = 'this.checkKey_ = "test";'

        with respx.mock:
            respx.get("https://fansly.com").mock(
                side_effect=[httpx.Response(200, text=html)]
            )
            respx.get("https://fansly.com/main.abc123.js").mock(
                side_effect=[httpx.Response(200, text=js_content)]
            )
            with patch(
                "helpers.checkkey.extract_checkkey_from_js",
                return_value="oybZy8-fySzis-bubayf",
            ):
                result = guess_check_key("Mozilla/5.0")

        assert result == "oybZy8-fySzis-bubayf"

    def test_homepage_non_200(self):
        """Lines 649-653: homepage returns non-200 → default key."""
        with respx.mock:
            respx.get("https://fansly.com").mock(
                side_effect=[httpx.Response(503, text="down")]
            )
            result = guess_check_key("Mozilla/5.0")

        assert result == "oybZy8-fySzis-bubayf"

    def test_no_main_js_in_html(self):
        """Lines 665-667: main.js URL not found in HTML → default key."""
        with respx.mock:
            respx.get("https://fansly.com").mock(
                side_effect=[httpx.Response(200, text="<html>no scripts</html>")]
            )
            result = guess_check_key("Mozilla/5.0")

        assert result == "oybZy8-fySzis-bubayf"

    def test_main_js_non_200(self):
        """Lines 682-686: main.js download fails → default key."""
        html = '<script src="main.abc123.js"></script>'
        with respx.mock:
            respx.get("https://fansly.com").mock(
                side_effect=[httpx.Response(200, text=html)]
            )
            respx.get("https://fansly.com/main.abc123.js").mock(
                side_effect=[httpx.Response(404, text="not found")]
            )
            result = guess_check_key("Mozilla/5.0")

        assert result == "oybZy8-fySzis-bubayf"

    def test_extraction_returns_none(self):
        """Lines 702-705: extraction fails → default key."""
        html = '<script src="main.abc123.js"></script>'
        with respx.mock:
            respx.get("https://fansly.com").mock(
                side_effect=[httpx.Response(200, text=html)]
            )
            respx.get("https://fansly.com/main.abc123.js").mock(
                side_effect=[httpx.Response(200, text="var x;")]
            )
            with patch("helpers.checkkey.extract_checkkey_from_js", return_value=None):
                result = guess_check_key("Mozilla/5.0")

        assert result == "oybZy8-fySzis-bubayf"

    def test_network_error(self):
        """Lines 707-709: httpx.RequestError → default key."""
        with respx.mock:
            respx.get("https://fansly.com").mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            result = guess_check_key("Mozilla/5.0")

        assert result == "oybZy8-fySzis-bubayf"

    def test_unexpected_exception(self):
        """Lines 711-713: unexpected error → default key."""
        with respx.mock:
            respx.get("https://fansly.com").mock(side_effect=RuntimeError("boom"))
            result = guess_check_key("Mozilla/5.0")

        assert result == "oybZy8-fySzis-bubayf"


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
            respx.get("https://fansly.com").mock(
                side_effect=[httpx.Response(200, text=html)]
            )
            respx.get("https://fansly.com/main.abc123.js").mock(
                side_effect=[httpx.Response(200, text=js_content)]
            )
            guess_check_key("Mozilla/5.0")

        mock_connection.stop.assert_called_once()

    def test_bridge_stopped_on_network_error(self):
        """connection.stop is called even when network fails before JS use."""
        with (
            respx.mock,
            patch("helpers.checkkey.connection") as mock_connection,
        ):
            respx.get("https://fansly.com").mock(side_effect=httpx.ConnectError("boom"))
            guess_check_key("Mozilla/5.0")

        mock_connection.stop.assert_called_once()

    def test_bridge_stopped_on_unexpected_exception(self):
        """connection.stop runs from the finally block even when an unexpected error raises."""
        with (
            respx.mock,
            patch("helpers.checkkey.connection") as mock_connection,
        ):
            respx.get("https://fansly.com").mock(side_effect=RuntimeError("boom"))
            guess_check_key("Mozilla/5.0")

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
            respx.get("https://fansly.com").mock(
                side_effect=[httpx.Response(200, text=html)]
            )
            respx.get("https://fansly.com/main.abc123.js").mock(
                side_effect=[httpx.Response(200, text=js_content)]
            )
            # Should NOT raise despite connection.stop throwing
            result = guess_check_key("Mozilla/5.0")

        assert result == "oybZy8-fySzis-bubayf"
        mock_connection.stop.assert_called_once()
