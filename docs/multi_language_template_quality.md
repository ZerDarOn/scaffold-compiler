# Multi-language template quality review

This pass changes generated templates, not the generator lifecycle. Existing user
projects and published releases are not rewritten.

## References and reuse

- SQLAlchemy async session documentation:
  https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- FastAPI dependency example:
  https://github.com/fastapi/full-stack-fastapi-template/blob/master/backend/app/api/deps.py
- GitHub CLI process entrypoint:
  https://github.com/cli/cli/blob/trunk/cmd/gh/main.go
- Go testing tutorial: https://go.dev/doc/tutorial/add-a-test
- CMake test registration: https://cmake.org/cmake/help/latest/command/add_test.html

These were references for resource lifetimes, a thin process entrypoint, and
native testing conventions. No third-party implementation was vendored or copied.

## Template changes

- PostgreSQL: use an explicit async transaction context instead of nested async
  iteration. Failure/cancellation rolls back and closes before returning, rather
  than depending on eventual generator finalization. Cover successful commit,
  request failure, cancellation, commit failure, and actual dependency athrow.
- Alembic: release the engine in finally, including connection/migration failure.
- C: checked integer addition rejects overflow and null output pointers while
  preserving the output value on failure. Boundary checks run even with NDEBUG.
  CLI checks output failures. README documents prerequisites and extension points.
- Go: separate process exit from run(io.Writer), propagate output errors, add
  table-driven arithmetic tests and command output/failure tests. README documents
  toolchain requirements, vet, formatting, and the sample's native integer semantics.
- Docker: reviewed its existing pinned multistage build, non-root runtime, and
  healthcheck. No speculative Docker changes were made without a running engine.

## Validation boundary

- Fresh PostgreSQL-capable Python project: 19 tests, Ruff lint/format, and strict
  mypy (21 source files) pass. Transaction tests use a fake session; no real
  PostgreSQL server was used. Existing two TestClient deprecation warnings remain.
- Go 1.27.1: gofmt, go test, go vet, build and executable output pass.
- C: Zig 0.16.0 C compiler with C11, Wall/Wextra/Wpedantic/Werror; optimized
  NDEBUG boundary-test executable passes, and the CLI builds/runs successfully.
  This is not a CMake/CTest or MSVC matrix run.
- Go/Zig archives were downloaded from official servers and SHA256 checked against
  their official release metadata. Tools live under ignored .scratch/toolchains;
  no global toolchain installation or PATH change was made.

The C example API and Python transaction helper changed intentionally. These are
new-project templates, not an automatic migration for existing generated projects.

## Follow-up: migration authoring and build verification

- Added the missing migrations/script.py.mako, with a project-owned minimal Mako
  implementation. Alembic's documented revision-generation interface was the
  reference: https://alembic.sqlalchemy.org/en/latest/tutorial.html
- Generated tests now create a revision in a temporary migration directory,
  render offline SQL, and verify that the revision passes Ruff lint and format.
  Standard Alembic post-write hooks apply the project's existing Ruff rules;
  migration authoring therefore requires the development dependencies.
- Alembic paths are relative to the configuration file, with path_separator=os.
  The README explains manual revisions, autogeneration metadata, and function-scoped
  database dependencies. HTTP tests verify that failed commits return 500 rather
  than an already-sent success response.
- Fresh PostgreSQL-capable project in a path with spaces: 22 tests and Ruff pass;
  strict mypy checks 21 source files. No live PostgreSQL server was involved.
- CMake 4.4.3 + Ninja 1.13.2 + Zig 0.16.0: Release build and both CTest tests pass.
  Zig compiler/archiver paths were supplied explicitly in an ignored temporary
  build; this does not claim an MSVC or cross-platform build matrix.
- Repository regression before the final migration formatting hooks: 335 passed,
  5 skipped, 293 subtests passed. Final hooks were verified in the generated project.
  Docker, live PostgreSQL, and POSIX-specific behavior remain unverified here.

## Follow-up: readiness failure and cancellation quality

- Database readiness now applies a two-second asyncio timeout around connection
  acquisition and SELECT 1. Driver/network OSError failures (including TimeoutError)
  return not-ready, alongside SQLAlchemy errors. Cancellation and programming
  errors propagate instead of being hidden. Async cleanup may extend elapsed time;
  the timeout is not a hard process-level deadline.
- Failure logs include only the exception category, not raw driver messages or
  connection URLs. Tests verify connection exit on query failure/timeout and engine
  disposal on both normal and exceptional lifespan exits.
- Reference: https://docs.python.org/3/library/asyncio-task.html#asyncio.timeout
- Fresh generated PostgreSQL-capable project: 32 tests pass; Ruff lint/format and
  strict mypy (22 source files) pass. Existing two TestClient warnings remain.
- Real asyncpg driver against a locally bound non-listening port: probe timed out,
  /health/ready returned 503 and /health/live returned 200. This checks an unavailable
  dependency, not successful integration with a live PostgreSQL server.
