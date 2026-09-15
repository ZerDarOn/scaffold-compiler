# Minimal FastAPI handoff acceptance

## Observed result

Tested the public v2.4.0 ZIP on Windows on 2026-09-16. The ZIP SHA-256 matched the
published checksum and GitHub asset digest. Selected the FastAPI recipe with database and delivery
both set to `none`, generated `First API`, and confirmed that the capsule, cleanup journal and
generation workspace disappeared after finalization.

From the generated project directory, followed its README:

- `uv sync --all-groups --frozen`: successful; Python 3.14.7 and locked dependencies installed.
- `uv run mypy`: passed, 16 files.
- `uv run pytest`: 3 passed, two dependency deprecation warnings.
- `uv run ruff check .` and `uv run ruff format --check .`: passed.
- `uv run uvicorn first_api.asgi:application --reload --port 8765`: started successfully.
- `/health/live`: HTTP 200, `{"status":"alive"}`.
- `/health/ready`: HTTP 200, `{"status":"ready"}`.
- `/openapi.json` and `/docs`: HTTP 200.
- `/`: HTTP 404, expected because no root route is defined.

Stopped the test server after HTTP checks. The generated project remains available independently
of the removed capsule. External users have not independently confirmed these results.

## Repairs based on the walkthrough

The capsule wizard suggested an unavailable `scaffold-compiler` command. The next-step commands
now use the actual Python/capsule invocation (or module invocation for source mode), with
PowerShell quoting on Windows and POSIX shell quoting elsewhere. A regression test executes the
displayed preview command from paths containing spaces, single quotes and a dollar sign.

The generated README now explains Python/uv requirements, working directory, initial network
access, API documentation and health URLs, the expected root 404, port selection and server shutdown.
The original published v2.4.0 artifact remains immutable; these repairs apply to future builds.

Focused regression: 29 tests and 8 subtests passed; format, lint and strict mypy passed.

## Remaining observations

The locked TestClient stack emits deprecation warnings about httpx and an AnyIO alias. They do not
prevent the current tests or HTTP checks from succeeding. Dependency migration is a separate change.
