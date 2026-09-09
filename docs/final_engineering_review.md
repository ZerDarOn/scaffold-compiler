# V2 final engineering review

Review date: 2026-09-08

Scope: `v1.0.2..HEAD`, covering the generic recipe architecture, C11/CMake reference recipe,
FastAPI compatibility path, disposable release lifecycle, failure recovery, documentation, tests,
and CI. Findings are ranked by impact to user data, security boundaries, correctness, and release
claims.

Status update (2026-09-09): this historical review qualified the V2 architecture before its first
public release. V2.0.0, V2.1.0, and V2.2.0 have since been published as stable releases after their exact
release commits, tag workflows, checksums, and downloaded assets passed the required gates.

V2.3 development review (2026-09-09): the trusted recipe registration work on `main` has no
unresolved high, medium, or low finding. FastAPI, CMake, and Go each expose one complete registration
unit; the composition root atomically compiles those units into recipe, answer, questionnaire,
assembly, validation, and runtime registries. Interactive initialization and runtime creation no
longer contain language dispatch branches. This remains an internal trusted-code extension model,
not a runtime plugin or manifest execution mechanism.

The V2.3 gate passed formatting and lint for 121 Python files, strict mypy for 121 source files, and
325 tests with 275 subtests. Four local skips are bounded to unavailable Docker/CMake tooling and
POSIX-only symlink semantics; the exact Phase 3 commit then passed Windows core, Ubuntu core, and
Linux real-release GitHub jobs. A transient Windows process-spawn failure was reproduced as
nonpersistent: the affected PostgreSQL capsule test and the following complete suite both passed.

## Outcome

No unresolved critical, high, or medium finding remains. V2 is suitable for a release-candidate
build after the explicit version and release-asset task. At the review time, it was not yet a
published V2 release.

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
| Recipe registration | Pass | Complete built-in units compile atomically; duplicate, incomplete, mismatched, unknown, or wrong-identity dispatch fails before recipe execution |

## Residual low-risk debt

- CMake 3.20, Ninja 1.10, and C11 are the declared floors, while real evidence currently comes from
  GitHub-managed Ubuntu and Windows images. A broader GCC/Clang/MSVC minimum-version matrix is still
  desirable before claiming those exact compiler floors individually.
- `V1CommandApplication` remains as a compatibility wrapper beside the generic application. It is
  covered by equivalence tests and should be removed only in a separately versioned compatibility
  decision, not during the V2 release-candidate change.
- Third-party recipe distribution, signatures, sandboxing, and runtime plugin loading remain
  explicitly out of scope. They require a separate trust protocol and must not weaken the closed
  built-in registration boundary.
- At the review time, the release candidate still needed an explicit version bump, deterministic
  capsule/archive/checksum build, clean-tree verification, and a separately authorized tag/GitHub
  Release action. Those gates were subsequently completed for V2.0.0, V2.1.0, and V2.2.0.

## Historical release gate

The gate required a chosen version and explicit authorization for public release side effects:
build assets first, rerun the full local gates, require all GitHub Actions jobs to pass for the
exact release commit, then tag and publish without modifying generated assets. The published stable
releases followed this sequence.
