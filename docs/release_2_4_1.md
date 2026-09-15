# 2.4.1 patch release

This patch contains the handoff fixes verified in `fastapi_usability_acceptance.md`:

- Wizard next-step commands use the actual Python/capsule entry, with shell-safe path quoting.
- Generated FastAPI README documents requirements, working directory, API URLs, expected root 404,
  port selection and server shutdown.

No changes to generation transactions, validation policy, dependencies, recovery or cleanup.
The fixes passed CI run `35003899643` at `6b3dfb6`; patch publication requires the frozen version's
CI and an independent downloaded-capsule check. `v2.4.0` remains the rollback point.

Status: version frozen; public tag and assets pending.
