# Scaffold Compiler

Scaffold Compiler compiles a constrained configuration and built-in blueprints into a verified,
standalone FastAPI project. It is a disposable generator, not a long-term project manager: after a
successful release run, an external supervisor removes the generator capsule. The finalized project
contains no generator, blueprint, journal, or run metadata.

V1 deliberately supports only four combinations:

| Database | Delivery | Status |
|---|---|---|
| None | Local | Runtime-verified |
| PostgreSQL | Local | Runtime-verified |
| None | Docker | Blueprint complete; real Docker gate pending |
| PostgreSQL | Docker Compose | Blueprint complete; real Docker gate pending |

The detailed architecture, guarantees, and remaining acceptance work are in
[`scaffold_compiler_implementation_plan.md`](scaffold_compiler_implementation_plan.md).

## Prerequisites

- Python 3.11 or newer outside the disposable capsule.
- `uv` 0.12.9 available on `PATH`, or its absolute path in `SCAFFOLD_COMPILER_UV`.
- For PostgreSQL projects, a disposable PostgreSQL database and a
  `postgresql+asyncpg://...` URL in `DATABASE_URL`.
- For Docker choices, a running Docker engine. Those delivery gates are still incomplete in this
  repository and currently cause generation to fail safely.

## Run a release capsule

Keep the distributed `capsule_manifest.json`, `scaffold_compiler.pyz`, and `blueprints` directory
together in their original capsule directory. Create `project.json` outside that directory:

```json
{
  "project_name": "Example API",
  "package_name": "example_api",
  "target_directory": "./example-api",
  "database": "none",
  "container": "none"
}
```

The target must not already exist, and its parent must exist. On Windows PowerShell:

```powershell
$env:SCAFFOLD_COMPILER_UV = (Get-Command uv).Source
python .\scaffold-compiler-capsule\scaffold_compiler.pyz run `
  --config .\project.json `
  --non-interactive `
  --confirm-finalize FINALIZE
```

For PostgreSQL, set `DATABASE_URL` before running:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://user:password@127.0.0.1:5432/example"
```

The URL is passed to controlled validation processes through the environment and is redacted from
captured process output. It is not written into the finalized project.

## Finalize and self-cleanup semantics

The command assembles and validates a candidate in a hidden, run-owned sibling workspace. Only a
candidate that passes every required gate receives a verification credential and is published with
a native no-replace directory move.

- Before publication, any failure leaves the target absent and preserves the workspace as evidence.
- After publication, cleanup failure never rolls back or deletes the finalized target.
- Generator self-cleanup is armed only after the project transaction reports success.
- The external supervisor waits for the generator process to exit, verifies the exact capsule
  manifest again, then deletes only manifest-owned files. A changed or extra entry blocks deletion.
- A failed run preserves the capsule so the failure can be inspected or retried.

Interactive lifecycle commands are not yet available in V1. The supported release path is the fully
confirmed non-interactive `run` command above.

## Development baseline

The generator has no third-party runtime dependency. Development tools live in a project-local
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
