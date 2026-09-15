# Scaffold Compiler 2.4.0-rc.1

Candidate for the V2.4 declarative recipe discovery release. The stable rollback point is v2.3.0.

## Changes

- `recipes` prints a deterministic schema-2 catalog for FastAPI, CMake, and Go.
- Ordered input descriptors expose types, choices, interactive defaults, and omission hints.
- All three built-in questionnaires share a descriptor-driven collector.
- Registration rejects invalid descriptors and duplicate input keys before generation.
- Source/capsule discovery equivalence is exercised in Windows and Linux CI.

## Compatibility

Existing configuration, generation, recovery, and capsule cleanup behavior is preserved.
FastAPI interactive defaults remain none/none; omitted fields in hand-written configuration retain
their existing postgres/docker defaults. Input metadata describes interactive behavior, not a full
configuration-validation schema. The answer normalizer remains authoritative.

Third-party runtime plugins and remote blueprints are outside this candidate.

## Distribution

The proposed tag is `v2.4.0-rc.1`. Public ZIP and SHA-256 assets must be built by the tag workflow
after the exact candidate commit passes quality gates. Local builds are preparation evidence only.
