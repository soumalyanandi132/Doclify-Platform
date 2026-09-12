# Development

This working copy extends upstream Doclify with a reusable documentation core.
See `docs/architecture/current-architecture.md` for scope and remaining work.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\doclify --help
```

Tests use injected model responses; no API key or paid inference is required.
Actual generation uses `GROQ_API_KEY` and sends selected source content to Groq.

## Library usage

```python
from pathlib import Path
from doclify.core import generate_documentation

result = generate_documentation(
    Path("/absolute/path/to/repository"),
    ["src/app.py"],
    project_name="Example",
)
print(result.markdown)
```

The caller chooses where to save results. The existing CLI still writes its
cache and README locally. `doclify update .` refreshes every configured file;
it is not a cache-only operation.
