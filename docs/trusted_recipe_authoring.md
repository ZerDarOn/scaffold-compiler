# Trusted built-in recipe authoring

This guide is for compiler maintainers adding a new language or project family. A recipe is a
trusted built-in product capability, not a runtime plugin. Blueprint manifests are declarative data
and cannot execute commands, import modules, register hooks, or download content. Executable behavior
must be reviewed Python code wired into a closed adapter registry.

## Architecture boundary

The reusable compiler owns the lifecycle:

1. parse and canonicalize a schema 2 configuration;
2. resolve recipe capabilities into an ordered blueprint plan;
3. assemble a candidate outside the target;
4. run recipe validation in a run-owned environment;
5. bind configuration, plan, blueprint, candidate, and verification digests;
6. publish once with no replacement;
7. clean only proven owned artifacts and then remove the release capsule.

A recipe owns only its answer contract, capability mapping, allowed blueprints, assembly adapter,
validation adapter, gates, and prerequisites. It must not fork the transaction, lock, state machine,
Finalize, failure recovery, or self-cleanup implementation.

## Recipe declaration

Declarations are currently compiled into `project_recipe_registry.py`. Every declaration uses
`schema_version` 1 and exactly these fields:

```json
{
  "schema_version": 1,
  "id": "example-cli",
  "version": "1.0.0",
  "answer_parser": "example-answers",
  "assembly_adapter": "example-v1-assembly",
  "validation_adapter": "example-v1-validation",
  "required_capabilities": ["example-project"],
  "capability_rules": [
    {"when": {"strict_mode": true}, "request": ["example-strict-mode"]}
  ],
  "allowed_blueprints": ["example-runtime", "example-build"],
  "allowed_validations": ["example-build", "example-test"],
  "prerequisites": ["Example tool 1.2 or newer"]
}
```

IDs and capabilities use lowercase hyphenated identifiers. Versions are numeric semantic versions.
Adapter values are opaque keys, never module or callable names. Registration succeeds only when
each key is present in the compiler's explicit trusted set. Conditions compare already-normalized
string or boolean answers; they cannot evaluate expressions.

`allowed_blueprints` and `allowed_validations` are security allowlists. The planner rejects a
dependency, capability provider, ordering edge, or gate that crosses the selected recipe scope.
Each capability has one provider, selected capabilities cannot conflict, and every output path has
one owner.

## Blueprint manifest

Place each blueprint in `blueprints/<blueprint-id>/blueprint.json`. Schema 2 is strict and requires
all fields:

```json
{
  "schema_version": 2,
  "id": "example-runtime",
  "version": "1.0.0",
  "provides": ["example-runtime"],
  "requires": ["example-quality"],
  "after": [],
  "conflicts": [],
  "files": [
    {"kind": "template", "source": "templates/main.txt.template", "target": "src/main.txt"},
    {"kind": "static", "source": "static/gitignore", "target": ".gitignore"}
  ],
  "variables": {"project_name": {"required": true, "type": "string"}},
  "contributions": {},
  "validations": ["example-build"]
}
```

Only `template` and `static` files exist. Sources must remain inside their blueprint directory;
targets must be unique safe relative paths. Template variables are explicitly declared and rendered
by the strict renderer. A manifest cannot execute a shell, embed a cleanup path, fetch a URL, or
select an adapter.

Use a blueprint for one cohesive capability. Put reusable project-quality files in a separate
blueprint only when their contents and semantics truly apply to that recipe; “common” must not mean
silently sharing language-specific assumptions.

## Trusted adapters

Adding a recipe requires three deliberate runtime registrations:

- An answer normalizer validates the recipe's complete answer object, rejects unknown fields, applies
  deterministic defaults, and returns JSON-only canonical values.
- An assembly adapter maps normalized answers and selected manifests to candidate bytes. It may use
  shared strict assembly utilities but must never write to the final target.
- A validation adapter maps only allowlisted gates to bounded, no-shell process specifications. It
  uses argument arrays, absolute discovered executables, timeouts, output limits, secret redaction,
  and the run-owned validation directory.

Register the keys in the built-in composition root. Do not add dynamic imports, entry points,
manifest-provided commands, arbitrary environment forwarding, or network installation hooks. Tool
installation is a user prerequisite; validation does not bootstrap a machine.

To expose the recipe through `init`, also register a fourth trusted-code component: a questionnaire
that collects JSON-only candidate answers. Questionnaire definitions must not come from blueprint
manifests and cannot execute hooks or tools. They are a convenience layer only; the registered
answer normalizer remains authoritative and must validate the complete result before any
configuration file is published. `init` must retain atomic no-overwrite behavior and must not start
generation or capsule cleanup.

## Required test evidence

Before a recipe can ship, add:

- configuration tests for defaults, invalid types, unknown answers, and portable identifiers;
- questionnaire tests for defaults, invalid-choice retry, interrupted input, strict parser reuse,
  and no-overwrite configuration publication;
- registry tests for trusted keys and recipe scope;
- planner tests for cross-recipe dependencies, conflicts, cycles, duplicate providers, and duplicate
  output ownership;
- golden-project tests for the complete tree, exact important bytes, and absence of unselected
  capabilities or compiler metadata;
- validation tests for missing tools, nonzero exits, timeouts, output limits, wrong runtime output,
  and process cleanup;
- a real release-capsule acceptance test that previews, generates, validates, publishes, runs the
  project, and observes capsule self-cleanup on every supported operating system;
- failure recovery tests proving the target stays absent before publication, intact afterward, and
  that repeated cleanup never touches sibling files.

Run the repository gates before review:

```powershell
.venv\Scripts\ruff.exe format --check .
.venv\Scripts\ruff.exe check .
.venv\Scripts\mypy.exe
.venv\Scripts\pytest.exe -p no:cacheprovider
```

## Review checklist

- The recipe has a narrow purpose and explicit prerequisites.
- Defaults are deterministic and all unknown configuration fields fail closed.
- Every blueprint, capability, gate, file, and adapter is allowlisted.
- Generated bytes contain no absolute source, workspace, capsule, secret, or generator paths.
- The validation adapter cannot execute user- or manifest-supplied commands.
- The target is written only by the shared no-replace publication step.
- Pre-publication failure preserves evidence; post-publication failure never deletes the target.
- Cleanup is bounded by immutable ownership records and rejects links, changed data, and extras.
- Windows and Linux evidence exists when both platforms are claimed.
- User documentation describes only behavior demonstrated by automated acceptance tests.
