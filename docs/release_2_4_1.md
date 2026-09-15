# 2.4.1 patch release

This patch contains the handoff fixes verified in `fastapi_usability_acceptance.md`:

- Wizard next-step commands use the actual Python/capsule entry, with shell-safe path quoting.
- Generated FastAPI README documents requirements, working directory, API URLs, expected root 404,
  port selection and server shutdown.

No changes to generation transactions, validation policy, dependencies, recovery or cleanup.
The fixes passed CI run `35003899643` at `6b3dfb6`; patch publication requires the frozen version's
CI and an independent downloaded-capsule check. `v2.4.0` remains the rollback point.

## Published and verified

- Frozen commit: `07b2533474a6d710795a2c788c1956783ce3a210`.
- Frozen-commit CI: `35012502511`, all jobs successful.
- Immutable tag: `v2.4.1`.
- Tag workflow: `35012912844`, all quality, build and publication jobs successful.
- Stable release: <https://github.com/ZerDarOn/scaffold-compiler/releases/tag/v2.4.1>.

The independently downloaded public ZIP passed integrity and checksum verification:

```text
ad9ae2c719e4c340203227288d9bb38a0bb4ef012be7258d16e71f9f8e8c1a3f
```

On Windows, executed the wizard's printed preview command with a configuration path containing
spaces, a single quote and a dollar sign. It succeeded. The downloaded capsule then generated and
validated a minimal FastAPI project; the resulting README contained requirements, `/docs`, expected
root 404 and shutdown instructions. Capsule, cleanup journal and generation workspace disappeared.

Status: patch published and public-asset acceptance complete. No further feature work is scheduled.
