# Stash Cleanup Enforcement Summary

This document summarizes the enforcement mechanisms ensuring all tests using `stash_client` also use `stash_cleanup_tracker`.

## Implementation Date

2025-11-05

## Problem Statement

Integration tests that use `stash_client` to connect to a live Stash instance must properly clean up any created objects to ensure:

- Test isolation (no cross-test contamination)
- No data accumulation in the Stash instance
- Consistent test environment state

**Critical**: This enforcement applies to **ALL** tests using `stash_client`, not just integration tests.
Even unit tests that mock the client must include `stash_cleanup_tracker` because:

- Incorrectly configured mocks can make real connections
- Partial mocks may allow some methods to reach the real server
- Developer error can bypass mocks entirely
- This project has historically left objects behind on the Docker Stash server

## Solution Components

### 1. Documentation Updates

**File: `tests/fixtures/stash_api_fixtures.py`**

- Updated `stash_cleanup_tracker` fixture docstring with:
  - IMPORTANT warning about cleanup tracker requirement when using `stash_client`
  - Reference to this enforcement summary
  - Usage example showing both fixtures together

### 2. Pytest Hook Enforcement

**File: `tests/conftest.py`**

- Added `pytest_collection_modifyitems` hook
- Automatically detects tests using `stash_client` without `stash_cleanup_tracker`
- Marks violating tests with `pytest.mark.xfail(strict=True)`
- Provides clear error message with documentation reference

**Hook Behavior:**

```python
def pytest_collection_modifyitems(config, items):
    """Hook to validate fixture usage and add markers."""
    for item in items:
        if (
            hasattr(item, "fixturenames")
            and "stash_client" in item.fixturenames
            and "stash_cleanup_tracker" not in item.fixturenames
        ):
            item.add_marker(
                pytest.mark.xfail(
                    reason="Tests using stash_client MUST also use stash_cleanup_tracker "
                    "for test isolation and cleanup. See tests/stash/CLEANUP_ENFORCEMENT_SUMMARY.md",
                    strict=True,
                )
            )
```

### 3. Enforcement Test

**File: `tests/stash/integration/test_cleanup_enforcement.py`**

- Deliberately violates the pattern (uses `stash_client` without cleanup)
- Demonstrates hook enforcement in action
- Documents expected behavior

**Expected Result:**

```
### 3. Enforcement Test

**File: `tests/stash/test_cleanup_enforcement.py`**
- Deliberately violates the pattern (uses `stash_client` without cleanup)
- Demonstrates hook enforcement in action
- Documents expected behavior

**Expected Result:**
```

FAILED tests/stash/test_cleanup_enforcement.py::test_missing_cleanup_tracker
[XPASS(strict)] Tests using stash_client MUST also use stash_cleanup_tracker
for test isolation and cleanup. See tests/stash/CLEANUP_ENFORCEMENT_SUMMARY.md

```

```

## How It Works

### Test Collection Phase

1. pytest collects all tests
2. `pytest_collection_modifyitems` hook runs
3. Hook examines each test's `fixturenames`
4. If test uses `stash_client` without `stash_cleanup_tracker`:
   - Adds xfail marker with strict=True
   - Includes helpful error message

### Test Execution Phase

1. If test marked as xfail and skips: **Expected** (Stash not available)
2. If test marked as xfail and fails: **Expected** (actual test failure)
3. If test marked as xfail but passes: **FAILURE** (violation detected)
   - strict=True converts unexpected pass to failure
   - Prevents accidental bypass of cleanup requirement

## Benefits

### Automatic Detection

- No manual review needed
- Catches violations during test collection
- Works in CI/CD pipelines

### Clear Guidance

- Error message explains the problem
- Points to documentation
- Shows correct pattern

### Prevention, Not Cure

- Blocks bad patterns before they cause problems
- Educates developers at write-time
- Maintains code quality standards

## Usage Examples

### ❌ Incorrect (Will Fail)

```python
@pytest.mark.asyncio
async def test_stash_operation(stash_client):
    # Missing cleanup tracker!
    result = await stash_client.execute(...)
```

**Result:** Test fails with enforcement message

### ✅ Correct (Will Pass)

```python
@pytest.mark.asyncio
async def test_stash_operation(stash_client, stash_cleanup_tracker):
    async with stash_cleanup_tracker(stash_client) as cleanup:
        # Create objects
        performer = await create_performer(stash_client, "Test")
        cleanup['performers'].append(performer['id'])

        # Test logic
        result = await stash_client.execute(...)

        # Cleanup happens automatically
```

**Result:** Test runs normally, cleanup occurs automatically

## Testing the Enforcement

### Run Enforcement Test

```bash
# This should show the enforcement in action
pytest tests/stash/test_cleanup_enforcement.py -v
```

### Run Valid Tests

```bash
# These should pass normally
pytest tests/stash/integration/ -v
```

### Run All Integration Tests

```bash
# All integration tests should follow the pattern
pytest tests/stash/integration/ -v
```

## Future Enhancements

Possible improvements:

1. **Auto-fix capability**: Suggest adding missing fixture in error message
2. **IDE integration**: LSP/linter plugin to detect at write-time
3. **Pre-commit hook**: Block commits with violating tests
4. **Metrics**: Track compliance rate across test suite

## Related Documentation

- **Cleanup Enforcement Summary**: `tests/stash/CLEANUP_ENFORCEMENT_SUMMARY.md` (this file)
- **Cleanup Fixture**: `tests/fixtures/stash_api_fixtures.py` (stash_cleanup_tracker)
- **Real Client Fixture**: `tests/fixtures/stash_api_fixtures.py` (stash_client, stash_context)
- **Integration Tests**: `tests/stash/integration/`

## Maintenance Notes

When modifying the enforcement mechanism:

1. Update this document
2. Update fixture docstrings in `tests/fixtures/stash_api_fixtures.py`
3. Test with both compliant and non-compliant tests
4. Verify error messages are clear and actionable
