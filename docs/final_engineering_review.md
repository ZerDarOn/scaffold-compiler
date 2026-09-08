# V2 final engineering review

Review date: 2026-09-08

Scope: `v1.0.2..HEAD`, covering the generic recipe architecture, C11/CMake reference recipe,
FastAPI compatibility path, disposable release lifecycle, failure recovery, documentation, tests,
and CI. Findings are ranked by impact to user data, security boundaries, correctness, and release
claims.

## Outcome

No unresolved critical, high, or medium finding remains. V2 is suitable for a release-candidate
build after the explicit version and release-asset task. It is not yet a published V2 release.

## Resolved findings

### Medium: cleanup-pending publication had no safe recovery command

After the target was published, a file lock or metadata deletion failure could leave the run in
`CLEANUP_PENDING`. `discard` correctly refused that state, but users had no bounded way to finish
temporary cleanup and capsule self-removal.

Resolution: the explicit `cleanup --confirm CLEANUP` command now accepts only an exactly named and
run-bound workspace. It verifies the published project against sealed candidate ownership before
deleting anything, preflights candidate and validation ownership, supports partial retry, never
deletes the target, and starts capsule self-cleanup only after success. Target changes, links,
unknown entries, invalid reports, invalid markers, and wrong states refuse safely.

Evidence: unit coverage for file-lock retry, partial ownership loading, repeated calls, changed
targets, unknown candidate entries, and wrong states; packaged C toolchain-failure recovery runs in
the cross-platform core CI job.

### Medium: complete candidate digest ignored unknown empty directories

The candidate digest rejected extra files and links but skipped ordinary directories. Snapshot
publication copied only manifest-owned files, so an extra empty directory could not reach the final
project, but the behavior was weaker than the complete-tree immutability contract.

Resolution: candidate verification now derives the only permitted parent directories from canonical
owned file paths. It rejects unknown directories, unsupported filesystem entries, symbolic links,
and Windows reparse points, including replacement of the candidate root itself. Expected paths are
safety-validated before traversal.

Evidence: regression tests prove an added empty directory and a root reparse point invalidate the
candidate; assembly, validation-adapter, Finalize, and cleanup suites pass with the stricter
invariant.

## Quality assessment

| Area | Result | Evidence |
|---|---|---|
| Correctness | Pass | Recipe/configuration/plan identities and all immutable digests are cross-bound; V1 and V2 FastAPI outputs are byte-equivalent |
| Security boundary | Pass | Recipes bind only explicit trusted adapter keys; manifests are strict data; no shell or dynamic adapter imports |
| Atomicity | Pass | One target lock domain, same-volume native no-replace publication, journal-first transitions, forward-only post-publication recovery |
| Deletion safety | Pass | Candidate, validation, failed-workspace, cleanup-recovery, and capsule deletion are ownership-scoped and reject changed/linked/extra entries |
| Process control | Pass | Argument arrays, `shell=False`, fixed timeouts, bounded output, process-tree termination, redaction, and isolated validation directories |
| Observability | Pass | Run/recipe/stage/gate/digest facts are logged without full user paths or secrets; persisted inspection reports stable failure facts |
| Compatibility | Pass | Legacy schema 1 maps to the FastAPI recipe; four V1/V2 configurations produce identical final bytes and cleanup behavior |
| Cross-platform evidence | Pass | Managed Ubuntu and Windows execute core quality plus real CMake configure/build/CTest/run; Linux executes four real FastAPI release combinations |
| Documentation | Pass | User and trusted-recipe-author guides are contract-tested against recipe IDs, commands, confirmations, and trust boundaries |

## Residual low-risk debt

- CMake 3.20, Ninja 1.10, and C11 are the declared floors, while real evidence currently comes from
  GitHub-managed Ubuntu and Windows images. A broader GCC/Clang/MSVC minimum-version matrix is still
  desirable before claiming those exact compiler floors individually.
- `V1CommandApplication` remains as a compatibility wrapper beside the generic application. It is
  covered by equivalence tests and should be removed only in a separately versioned compatibility
  decision, not during the V2 release-candidate change.
- The release candidate still needs an explicit version bump, deterministic capsule/archive/checksum
  build, clean-tree verification, and a separately authorized tag/GitHub Release action.

## Release gate

Proceed to Task 7.3 only when the release version is chosen and public release side effects are
explicitly authorized. Build assets first, rerun the full local gates, require all GitHub Actions
jobs to pass for the exact release commit, then tag and publish without modifying generated assets.
