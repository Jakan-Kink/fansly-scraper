# CheckKey Extraction with JSPyBridge

## Overview

The checkKey extraction now uses **JSPyBridge** for efficient Python-JavaScript communication, with a fallback to subprocess if JSPyBridge is not available.

## Installation

### Option 1: JSPyBridge (Recommended)

```bash
# Install JSPyBridge
pip install javascript

# Install JavaScript dependencies
npm install acorn acorn-walk
```

### Option 2: Subprocess Fallback

If you don't want to use JSPyBridge, the code will automatically fall back to subprocess:

```bash
# Just install Node.js dependencies
npm install acorn acorn-walk
```

## How It Works

### With JSPyBridge (Primary)

```python
from javascript import require

# Import JavaScript libraries directly in Python
acorn = require("acorn")
acorn_walk = require("acorn-walk")

# Parse JavaScript
ast = acorn.parse(js_content, {"ecmaVersion": 2020, "sourceType": "script"})

# Walk AST to find assignments
def check_assignment(node):
    if node.left.property.name == "checkKey_":
        # Found it!
        ...

acorn_walk.simple(ast, {"AssignmentExpression": check_assignment})

# Execute expression
from javascript import eval_js
checkkey = eval_js(expression)  # Fast JavaScript execution!
```

### Without JSPyBridge (Fallback)

If JSPyBridge is not installed, the code automatically falls back to subprocess:

```python
# Spawns Node.js process
subprocess.run(["node", "temp_script.js"], ...)
```

## Performance Comparison

| Method         | Speed     | Overhead                          | Best For         |
| -------------- | --------- | --------------------------------- | ---------------- |
| **JSPyBridge** | ⚡ Fast   | ✅ Low (persistent Node.js)       | Production use   |
| **Subprocess** | 🐢 Slower | ❌ High (spawn Node.js each time) | Fallback/testing |

## Usage

### Direct Usage

```python
from helpers.checkkey import extract_checkkey_from_js

# Extract from JavaScript content
checkkey = extract_checkkey_from_js(js_content)
# Returns: "oybZy8-fySzis-bubayf"
```

### Via Configuration

```python
from helpers.checkkey import guess_check_key

# Download and extract automatically
checkkey = guess_check_key(user_agent)
# Returns: "oybZy8-fySzis-bubayf"
```

## Code Structure

```python
# Import with fallback
try:
    from javascript import require
    acorn = require("acorn")
    acorn_walk = require("acorn-walk")
    HAS_JSPYBRIDGE = True
except ImportError:
    HAS_JSPYBRIDGE = False

def extract_checkkey_from_js(js_content: str) -> str | None:
    if not HAS_JSPYBRIDGE:
        # Fallback to subprocess
        return _extract_checkkey_subprocess(js_content)

    # Use JSPyBridge for fast extraction
    ast = acorn.parse(js_content, {...})
    acorn_walk.simple(ast, {...})
    return eval_js(expression)
```

## Benefits of JSPyBridge

### 1. **No Process Spawning**

- ✅ JSPyBridge: Persistent Node.js process
- ❌ Subprocess: New process each call

### 2. **Direct Integration**

```python
# JSPyBridge - feels native!
acorn = require("acorn")
result = acorn.parse(code)

# Subprocess - complex!
result = subprocess.run(["node", "-e", "..."], ...)
```

### 3. **Better Error Handling**

```python
# JSPyBridge - Python exceptions
try:
    ast = acorn.parse(js_content)
except Exception as e:
    print(f"Parse error: {e}")

# Subprocess - check return codes
if result.returncode != 0:
    print(result.stderr)
```

### 4. **Type Safety**

```python
# JSPyBridge - Python objects
checkkey: str = eval_js(expression)

# Subprocess - JSON parsing
checkkey = json.loads(result.stdout)["checkkey"]
```

## Migration from Subprocess

### Old (Subprocess Only)

```python
import subprocess

result = subprocess.run(
    ["node", "-e", f"console.log({expression})"],
    capture_output=True,
    text=True,
    timeout=2
)
checkkey = result.stdout.strip()
```

### New (JSPyBridge with Fallback)

```python
from javascript import require, eval_js

acorn = require("acorn")
# ... parse and find expression ...
checkkey = eval_js(expression)  # Fast!
```

## Troubleshooting

### JSPyBridge Not Found

```
ImportError: No module named 'javascript'
```

**Solution**: Install JSPyBridge

```bash
pip install javascript
```

### Acorn Not Found

```
Error: Cannot find module 'acorn'
```

**Solution**: Install npm packages

```bash
npm install acorn acorn-walk
```

### Falls Back to Subprocess

If you see this warning:

```
Node.js not found. Install Node.js and run: npm install acorn acorn-walk
Or install JSPyBridge: pip install javascript
```

**Solution**: Either:

1. Install JSPyBridge: `pip install javascript`
2. Or install Node.js and run: `npm install acorn acorn-walk`

## Testing

Test both methods work:

```bash
# Test with JSPyBridge
python3 -c "from helpers.checkkey import extract_checkkey_from_js; print('JSPyBridge works!')"

# Test with subprocess fallback (uninstall JSPyBridge temporarily)
pip uninstall javascript
python3 -c "from helpers.checkkey import extract_checkkey_from_js; print('Subprocess works!')"
```

## Summary

**Primary Method**: JSPyBridge

- ✅ Fast (no process spawning)
- ✅ Clean API (direct `require()`)
- ✅ Better error handling
- ✅ Type-safe results

**Fallback Method**: Subprocess

- ✅ Works without JSPyBridge
- ✅ Same AST logic
- ⚠️ Slower (spawns Node.js)
- ⚠️ More complex

**Installation**:

```bash
# Recommended
pip install javascript
npm install acorn acorn-walk

# Minimum (fallback only)
npm install acorn acorn-walk
```

🎯 **Best of both worlds**: Fast JSPyBridge with automatic subprocess fallback!
