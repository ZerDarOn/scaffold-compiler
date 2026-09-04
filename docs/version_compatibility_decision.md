# Version compatibility decision

Decision date: 2026-09-04

## Decision

- The generator runs on Python 3.11 or newer and uses only the Python standard library at runtime.
- Generated FastAPI projects target Python 3.14.
- Every direct generator-development and generated-project dependency is pinned exactly.
- Transitive dependencies are frozen by `uv.lock` when the corresponding project variant exists.
- SQLAlchemy remains on the stable 2.0 line; the 2.1 release candidate is not used.
- PostgreSQL access uses SQLAlchemy asyncio with asyncpg.

## Frozen versions

| Purpose | Package | Version |
|---|---|---:|
| Generator build backend | setuptools | 84.0.0 |
| Generator resolver | uv | 0.12.9 |
| Generator quality | pytest | 9.1.1 |
| Generator quality | Ruff | 0.16.6 |
| Generator quality | mypy | 2.3.1 |
| FastAPI runtime | FastAPI | 0.141.1 |
| FastAPI runtime | Uvicorn | 0.52.4 |
| FastAPI runtime | pydantic-settings | 2.15.0 |
| PostgreSQL runtime | SQLAlchemy | 2.0.52 |
| PostgreSQL runtime | Alembic | 1.19.1 |
| PostgreSQL runtime | asyncpg | 0.31.0 |
| Generated-project tests | HTTPX | 0.28.1 |
| Generated-project quality | pytest | 9.1.1 |
| Generated-project quality | Ruff | 0.16.6 |
| Generated-project quality | mypy | 2.3.1 |

## Compatibility evidence

- The [Python development guide](https://devguide.python.org/versions/) lists Python 3.14 in the
  stable bugfix phase, with support scheduled through October 2030. Python 3.15 is still a
  prerelease and is excluded.
- [FastAPI 0.141.1](https://pypi.org/project/fastapi/0.141.1/) requires Python 3.10 or newer.
- [Uvicorn 0.52.4](https://pypi.org/project/uvicorn/0.52.4/) requires Python 3.10 or newer.
- [pydantic-settings 2.15.0](https://pypi.org/project/pydantic-settings/2.15.0/) requires Python 3.10
  or newer.
- [SQLAlchemy 2.0.52](https://pypi.org/project/SQLAlchemy/2.0.52/) is the current stable 2.0 release;
  2.1.0 was a release candidate at the decision date.
- [Alembic 1.19.1](https://pypi.org/project/alembic/1.19.1/) requires Python 3.10 or newer.
- [asyncpg 0.31.0](https://pypi.org/project/asyncpg/0.31.0/) publishes CPython 3.14 wheels for
  Windows x86-64 and Linux x86-64.
- [uv 0.12.9](https://pypi.org/project/uv/0.12.9/) is the frozen resolver used to produce lock files.

PyPI project metadata was queried directly for all versions above. The generated-project lock
files are intentionally deferred until the four variants are implemented in Tasks 4.3 through
4.5.
