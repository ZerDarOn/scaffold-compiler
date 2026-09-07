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
| None | Docker | Runtime-verified on Linux CI |
| PostgreSQL | Docker Compose | Runtime-verified on Linux CI |

The detailed architecture, guarantees, and remaining acceptance work are in
[`scaffold_compiler_implementation_plan.md`](scaffold_compiler_implementation_plan.md).

## Prerequisites

- Python 3.11 or newer outside the disposable capsule.
- `uv` 0.12.9 available on `PATH`, or its absolute path in `SCAFFOLD_COMPILER_UV`.
- For PostgreSQL projects, a disposable PostgreSQL database and a
  `postgresql+asyncpg://...` URL in `DATABASE_URL`.
- For Docker choices, a Docker CLI and running engine. The CLI may be supplied by absolute path in
  `SCAFFOLD_COMPILER_DOCKER`. M-02/M-04 acceptance builds and runs real containers; an unavailable
  engine or incomplete cleanup causes generation to fail safely.

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

## Preview and recover a failed run

`preview` validates the configuration and prints deterministic JSON containing the selected
blueprints and required gates. It does not require `uv` and creates no workspace or target:

```powershell
python .\scaffold-compiler-capsule\scaffold_compiler.pyz preview `
  --config .\project.json
```

When `run` fails, its error names the preserved hidden workspace. Inspect its bound state, candidate
digest, and failed gates without needing `uv` or database credentials:

```powershell
python .\scaffold-compiler-capsule\scaffold_compiler.pyz inspect `
  --workspace .\.example-api.scaffold-<run-id>
```

After diagnosis, explicitly discard only that failed workspace:

```powershell
python .\scaffold-compiler-capsule\scaffold_compiler.pyz discard `
  --workspace .\.example-api.scaffold-<run-id> `
  --confirm DISCARD
```

Discard first validates the session binding, workspace name, allowed root entries, candidate file
hashes, and validation-environment marker. Any changed, linked, extra, or ambiguous entry blocks
cleanup. Committed, cleanup-pending, and ambiguous sessions are never discardable.

## Finalize and self-cleanup semantics

The command assembles and validates a candidate in a hidden, run-owned sibling workspace. Only a
candidate that passes every required gate receives a verification credential and is published with
a native no-replace directory move.

- Before publication, any failure leaves the target absent and preserves the workspace as evidence.
- After publication, cleanup failure never rolls back or deletes the finalized target.
- Generator self-cleanup is armed only after the project transaction reports success.
- The external supervisor waits for the generator process to exit, verifies the exact capsule
  manifest again, then deletes only manifest-owned files. A changed or extra entry blocks deletion.
- A failed run preserves the capsule so the evidence can be inspected, explicitly discarded, or the
  generation retried as a new run.

V1 intentionally exposes only `preview`, `inspect`, `discard`, and the fully confirmed `run` path.
It does not claim to manage or upgrade a project after Finalize.

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

The repository workflow runs formatting, lint, typing, and core tests on both Windows and Linux. A
separate Linux release job requires a working Docker engine and Compose, provisions an isolated
PostgreSQL 18.1 service, and executes the four real capsule combinations. Missing container
capabilities fail that job before acceptance starts; they are not treated as a passing skip.
The same workflow has passed the complete core suite on Windows and Linux and the real M-01 through
M-04 release matrix on Linux.

Exact tool and generated-project dependency versions are recorded in
[`docs/version_compatibility_decision.md`](docs/version_compatibility_decision.md).

## Build a versioned release capsule

Maintainers can build the current version without overwriting an existing artifact:

```powershell
New-Item -ItemType Directory -Path dist
.venv\Scripts\python -m scaffold_compiler.release_capsule_command `
  --destination .\dist\scaffold-compiler-1.0.1 `
  --archive
```

The command derives the capsule identity from the package version, builds the deterministic
directory, verifies its manifest, and optionally emits a byte-stable ZIP plus a `.sha256` checksum.
The `release-capsule` GitHub workflow reruns the complete quality workflow before uploading the
directory and both release assets as an Actions artifact. A tag must exactly match
`v<package-version>`. Tag-triggered runs then publish the ZIP and checksum as a GitHub Release;
manual workflow runs remain artifact-only and cannot publish a release.
