# Python template quality review

Scope: the generated FastAPI core, not new generator features or business modules.

## Reference review

- Starlette ServerErrorMiddleware:
  https://github.com/encode/starlette/blob/main/starlette/middleware/errors.py
  Unhandled exceptions are processed outside user middleware. Keep a request-scoped
  ID in ASGI state so the error handler can correlate its response and log after
  the ContextVar has been reset.
- ASGI Correlation ID:
  https://github.com/snok/asgi-correlation-id/blob/main/asgi_correlation_id/middleware.py
  Reviewed header validation and response correlation. Keep the local dependency-free
  implementation and explicit context cleanup; no upstream implementation was copied.
- FastAPI full-stack template:
  https://github.com/fastapi/full-stack-fastapi-template/blob/master/backend/app/main.py
  Reviewed application composition. Retain the smaller application factory and
  optional CORS instead of importing unrelated full-stack business features.

## Changes

- JSON logging preserves exception tracebacks and explicit request IDs, using the
  record creation timestamp instead of formatting time.
- Unexpected 500 responses and application error logs retain the same request ID.
- Invalid header bytes are not silently removed; response IDs replace conflicting
  values rather than creating duplicate headers.
- ApplicationError uses normal Exception initialization and allows standard
  traceback propagation through context managers.
- Generated tests cover expected/unexpected errors, log formatting, bounded request
  IDs, concurrent context isolation, context reset on success/failure, and CORS.

## Verification

- Freshly generated minimal FastAPI project: 14 tests passed; Ruff lint and format
  passed; strict mypy passed (16 source files). Used the existing Python 3.14
  environment with the template's pinned dependencies.
- Repository suite: 334 passed, 6 skipped, 293 subtests passed. Includes real minimal
  capsule generation and cleanup. UV_PYTHON and UV_PYTHON_INSTALL_DIR were set for
  the test process to use the working workspace Python installation; the global
  uv Python link is broken. No global configuration was changed.
- The final two generated concurrency tests were separately run after the full
  suite's capsule had been built.
- Skipped environment-dependent checks: C toolchain, Go, Docker (two scenarios),
  PostgreSQL, and POSIX symlink behavior. These are not claimed as verified here.
- Two existing TestClient dependency deprecation warnings remain; no dependency
  upgrade or Python support-range expansion is included.

Changes are in source templates. Existing user projects and previously published
release assets are not modified. No release publication is part of this review.
