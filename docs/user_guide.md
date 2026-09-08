# User guide

Scaffold Compiler turns one explicit JSON configuration into a verified standalone project. Choose
one trusted built-in recipe, preview the exact blueprint and validation plan, then run the confirmed
transaction. A successful release leaves only the generated project; the disposable compiler
capsule removes itself through an external, manifest-bound supervisor.

## Choose a recipe

| Recipe ID | Generates | Required local tools |
|---|---|---|
| `python-fastapi-service` | Python 3.14 FastAPI service | Python 3.11+ for the capsule and uv 0.12.9 |
| `c-cmake-cli` | C11 command-line project with CMake presets and tests | Python 3.11+, CMake 3.20+, Ninja 1.10+, CTest, and a C11 compiler |
| `go-cli` | Standard-library Go command-line project with tests | Python 3.11+ and Go 1.22+ |

FastAPI can optionally add PostgreSQL persistence and Docker delivery. The C recipe can optionally
enable compiler-specific strict warnings. Recipe choice is made once per run; capabilities from one
recipe cannot be mixed into another.

The Go recipe asks for a portable lowercase binary name and module path. Defaults are derived from
the project name as `example.com/<binary-name>`. Validation fixes `GOTOOLCHAIN=local` and
`GOWORK=off`; it does not download project dependencies or write build output into the candidate.

## Create a configuration interactively

Keep `capsule_manifest.json`, `scaffold_compiler.pyz`, and `blueprints` together, then run:

```powershell
python .\scaffold-compiler-capsule\scaffold_compiler.pyz init `
  --output .\project.json
```

`init` asks which trusted built-in recipe to use, then asks only the common and recipe-specific
questions needed for that choice. It validates the completed answers with the production schema-2
parser and writes canonical JSON. The command does not create the target project or hidden
workspace, does not check external build tools, and does not arm capsule self-cleanup. The output
file's parent must already exist, and an existing file is never overwritten. Interrupted or invalid
input leaves no partial configuration.

The completion message prints the exact next `preview` command and the explicitly confirmed `run`
command. Review the generated JSON before using either one.

## Write a configuration manually

Use schema 2 for all new projects. The target directory must not exist, while its parent must exist.
Relative paths are resolved from the command's working directory. Specify answers explicitly when
you want the configuration to remain obvious during review.

### FastAPI without PostgreSQL or Docker

```json
{
  "schema_version": 2,
  "recipe": "python-fastapi-service",
  "project_name": "Example API",
  "target_directory": "./example-api",
  "answers": {
    "package_name": "example_api",
    "database": "none",
    "delivery": "none"
  }
}
```

FastAPI answers are:

- `package_name`: a valid normalized Python package name; when omitted it is derived from the
  project name.
- `database`: `none` or `postgres`; the current default is `postgres`.
- `delivery`: `none` or `docker`; the current default is `docker`.

When `database` is `postgres`, provide a disposable validation database through `DATABASE_URL`.
When `delivery` is `docker`, a running Docker engine is required. If both are selected, validation
also exercises the generated Compose deployment.

### C11/CMake CLI

```json
{
  "schema_version": 2,
  "recipe": "c-cmake-cli",
  "project_name": "Example CLI",
  "target_directory": "./example-cli",
  "answers": {
    "target_name": "example_cli",
    "strict_warnings": true
  }
}
```

C recipe answers are:

- `target_name`: a portable lowercase C identifier matching `[a-z][a-z0-9_]{0,62}`; when omitted
  it is derived from the project name.
- `strict_warnings`: a JSON boolean; the default is `true`.

Schema 1 FastAPI configuration remains accepted for compatibility, but it cannot select another
recipe. Do not mix legacy fields (`package_name`, `database`, `container`) with schema 2 fields.

## Preview before creating anything

Preview validates the configuration and prints deterministic JSON without creating a target or
hidden workspace:

```powershell
python .\scaffold-compiler-capsule\scaffold_compiler.pyz preview `
  --config .\project.json
```

Review `recipe`, `recipe_version`, `blueprints`, `validations`, `target_name`, and
`configuration_digest` in the output. An unexpected item means the configuration should be fixed
before `run`.

## Run the verified transaction

Tools are discovered on `PATH`. Absolute executable overrides are available for controlled or
multi-install environments:

```powershell
$env:SCAFFOLD_COMPILER_UV = (Get-Command uv).Source
$env:SCAFFOLD_COMPILER_DOCKER = (Get-Command docker).Source
$env:SCAFFOLD_COMPILER_CMAKE = (Get-Command cmake).Source
$env:SCAFFOLD_COMPILER_CTEST = (Get-Command ctest).Source
$env:SCAFFOLD_COMPILER_GO = (Get-Command go).Source
$env:SCAFFOLD_COMPILER_GOFMT = (Get-Command gofmt).Source
```

Set only the overrides needed by the selected recipe, then execute:

```powershell
python .\scaffold-compiler-capsule\scaffold_compiler.pyz run `
  --config .\project.json `
  --non-interactive `
  --confirm-finalize FINALIZE
```

The compiler assembles a candidate in a hidden sibling workspace, runs every required gate, seals
the candidate digest, and publishes with a native no-replace directory move. It never overlays an
existing target. Generation metadata, blueprints, caches, validation environments, and the compiler
are not copied into the project.

## Recover a failed or interrupted run

A failed command prints the preserved hidden workspace name. Use exactly that path.

First inspect the bound state and failed gates:

```powershell
python .\scaffold-compiler-capsule\scaffold_compiler.pyz inspect `
  --workspace .\.example-api.scaffold-<run-id>
```

If publication never occurred and the state is safely discardable, remove only the failed evidence:

```powershell
python .\scaffold-compiler-capsule\scaffold_compiler.pyz discard `
  --workspace .\.example-api.scaffold-<run-id> `
  --confirm DISCARD
```

If publication succeeded but temporary cleanup was interrupted, retry the cleanup transaction:

```powershell
python .\scaffold-compiler-capsule\scaffold_compiler.pyz cleanup `
  --workspace .\.example-api.scaffold-<run-id> `
  --confirm CLEANUP
```

`cleanup` verifies the published target byte for byte against sealed ownership evidence before any
deletion. It removes only unchanged, manifest-owned temporary files and does not delete the
published target. Unknown files, links, changed bytes, invalid markers, mismatched reports, and the
wrong session state cause refusal. A successful retry also starts capsule self-cleanup.

| Observed state | Safe action |
|---|---|
| `failed_retryable` before publication | Inspect, then `discard` if the evidence remains valid |
| `cleanup_pending` after publication | Leave the target untouched and use `cleanup` |
| ambiguous or invalid evidence | Stop; preserve both target and workspace for manual diagnosis |
| workspace already absent | No recovery action is needed; repeated recovery returns failure without deleting neighbors |

Do not manually rename, edit, or add files inside a preserved workspace before recovery. If the
published project was intentionally edited, keep it and diagnose/remove the temporary workspace
manually only after independently establishing ownership; the compiler will correctly refuse it.

## What the compiler does not do

The compiler is not a daemon, dependency updater, migration manager, or long-term project platform.
It does not inject an agent into generated projects and cannot later modify them. Its boundary ends
after verified publication and exact cleanup.
