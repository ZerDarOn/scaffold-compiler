# Scaffold Compiler

Scaffold Compiler turns a constrained configuration and built-in blueprints into a verified,
standalone FastAPI project. The generator is intentionally absent from the finalized project.

The architecture and execution sequence are defined in
[`scaffold_compiler_implementation_plan.md`](scaffold_compiler_implementation_plan.md).

## Development baseline

The generator has no runtime dependencies. Development tools are installed into a project-local
virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install uv==0.12.9
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
.venv\Scripts\uv sync --extra dev --locked
.venv\Scripts\uv run ruff format --check .
.venv\Scripts\uv run ruff check .
.venv\Scripts\uv run mypy
.venv\Scripts\uv run pytest
```

Exact tool and generated-project dependency versions are recorded in
[`docs/version_compatibility_decision.md`](docs/version_compatibility_decision.md).
