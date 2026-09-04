"""Strict loading for bundled, declarative blueprint manifests."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

BLUEPRINT_SCHEMA_VERSION: Final = 1
_IDENTIFIER_PATTERN: Final = re.compile(r"^[a-z][a-z0-9-]*$")
_VERSION_PATTERN: Final = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_MANIFEST_FIELDS: Final = frozenset(
    {
        "schema_version",
        "id",
        "version",
        "stage",
        "provides",
        "requires",
        "conflicts",
        "variables",
        "files",
        "contributions",
        "validations",
    }
)


class ApplicationStage(StrEnum):
    """The fixed cross-blueprint application stages, in execution order."""

    PROJECT_QUALITY = "project-quality"
    PYTHON_RUNTIME = "python-runtime"
    FASTAPI_HTTP_API = "fastapi-http-api"
    POSTGRES_PERSISTENCE = "postgres-persistence"
    DOCKER_DELIVERY = "docker-delivery"
    POSTGRES_DOCKER_INTEGRATION = "postgres-docker-integration"
    FINAL_PROJECT_ASSEMBLY = "final-project-assembly"

    @property
    def order(self) -> int:
        return tuple(ApplicationStage).index(self)


class BlueprintFileKind(StrEnum):
    """Supported non-executable blueprint file forms."""

    TEMPLATE = "template"
    STATIC = "static"


class BlueprintCatalogError(ValueError):
    """Raised when a bundled manifest is malformed or ambiguous."""


@dataclass(frozen=True, slots=True)
class BlueprintFile:
    """One exclusively owned output file declared by a blueprint."""

    target: str
    source: str
    kind: BlueprintFileKind


@dataclass(frozen=True, slots=True)
class BlueprintManifest:
    """Normalized immutable blueprint metadata."""

    blueprint_id: str
    version: str
    stage: ApplicationStage
    provides: tuple[str, ...]
    requires: tuple[str, ...]
    conflicts: tuple[str, ...]
    files: tuple[BlueprintFile, ...]
    variables: tuple[str, ...]
    variables_json: str
    contributions_json: str
    validations: tuple[str, ...]
    directory: Path


@dataclass(frozen=True, slots=True)
class BlueprintCatalog:
    """A version-frozen collection of uniquely identified blueprints."""

    manifests: tuple[BlueprintManifest, ...]

    def by_id(self, blueprint_id: str) -> BlueprintManifest:
        for manifest in self.manifests:
            if manifest.blueprint_id == blueprint_id:
                return manifest
        raise KeyError(blueprint_id)


def load_blueprint_catalog(root: Path) -> BlueprintCatalog:
    """Load every manifest below a bundled catalog root with a strict schema."""
    if not root.is_dir():
        raise BlueprintCatalogError("Blueprint catalog root must be a directory.")

    manifests: list[BlueprintManifest] = []
    seen_ids: set[str] = set()
    manifest_paths = sorted(root.glob("*/blueprint.json"), key=lambda path: path.as_posix())
    if not manifest_paths:
        raise BlueprintCatalogError("Blueprint catalog contains no manifests.")

    for manifest_path in manifest_paths:
        record = _load_json_object(manifest_path)
        manifest = _parse_manifest(record, manifest_path.parent)
        if manifest.blueprint_id in seen_ids:
            raise BlueprintCatalogError(f"Duplicate blueprint ID: {manifest.blueprint_id}.")
        seen_ids.add(manifest.blueprint_id)
        manifests.append(manifest)

    return BlueprintCatalog(tuple(sorted(manifests, key=lambda item: item.blueprint_id)))


def _load_json_object(path: Path) -> dict[str, object]:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise BlueprintCatalogError(f"Duplicate JSON field: {key}.")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BlueprintCatalogError(f"Cannot read blueprint manifest: {path.name}.") from error
    if not isinstance(value, dict):
        raise BlueprintCatalogError("Blueprint manifest must be a JSON object.")
    return value


def _parse_manifest(record: dict[str, object], directory: Path) -> BlueprintManifest:
    if set(record) != _MANIFEST_FIELDS:
        unexpected = sorted(set(record) - _MANIFEST_FIELDS)
        missing = sorted(_MANIFEST_FIELDS - set(record))
        raise BlueprintCatalogError(
            f"Blueprint manifest fields do not match the schema; missing={missing}, "
            f"unexpected={unexpected}."
        )
    if record["schema_version"] != BLUEPRINT_SCHEMA_VERSION:
        raise BlueprintCatalogError("Unsupported blueprint schema version.")

    blueprint_id = _required_string(record, "id")
    version = _required_string(record, "version")
    if not _IDENTIFIER_PATTERN.fullmatch(blueprint_id):
        raise BlueprintCatalogError("Blueprint ID is invalid.")
    if not _VERSION_PATTERN.fullmatch(version):
        raise BlueprintCatalogError("Blueprint version must use numeric semantic versioning.")

    try:
        stage = ApplicationStage(_required_string(record, "stage"))
    except ValueError as error:
        raise BlueprintCatalogError("Blueprint application stage is invalid.") from error

    variables = record["variables"]
    contributions = record["contributions"]
    if not isinstance(variables, dict) or not isinstance(contributions, dict):
        raise BlueprintCatalogError("Variables and contributions must be JSON objects.")
    if any(
        not isinstance(name, str) or not _IDENTIFIER_PATTERN.fullmatch(name.replace("_", "-"))
        for name in variables
    ):
        raise BlueprintCatalogError("Blueprint variable name is invalid.")

    files = _parse_files(record["files"])
    return BlueprintManifest(
        blueprint_id=blueprint_id,
        version=version,
        stage=stage,
        provides=_string_tuple(record, "provides", require_nonempty=True),
        requires=_string_tuple(record, "requires"),
        conflicts=_string_tuple(record, "conflicts"),
        files=files,
        variables=tuple(sorted(variables)),
        variables_json=_canonical_json(variables),
        contributions_json=_canonical_json(contributions),
        validations=_string_tuple(record, "validations"),
        directory=directory,
    )


def _parse_files(value: object) -> tuple[BlueprintFile, ...]:
    if not isinstance(value, list):
        raise BlueprintCatalogError("Blueprint files must be an array.")
    files: list[BlueprintFile] = []
    targets: set[str] = set()
    for raw_file in value:
        if not isinstance(raw_file, dict) or set(raw_file) != {"target", "source", "kind"}:
            raise BlueprintCatalogError("Blueprint file entries do not match the schema.")
        target = _required_string(raw_file, "target")
        source = _required_string(raw_file, "source")
        if not target or not source:
            raise BlueprintCatalogError("Blueprint file paths cannot be empty.")
        if target in targets:
            raise BlueprintCatalogError(f"Duplicate target within blueprint: {target}.")
        targets.add(target)
        try:
            kind = BlueprintFileKind(_required_string(raw_file, "kind"))
        except ValueError as error:
            raise BlueprintCatalogError("Blueprint file kind is invalid.") from error
        files.append(BlueprintFile(target=target, source=source, kind=kind))
    return tuple(sorted(files, key=lambda item: item.target))


def _required_string(record: dict[str, object], field: str) -> str:
    value = record[field]
    if not isinstance(value, str):
        raise BlueprintCatalogError(f"Blueprint field {field} must be a string.")
    return value


def _string_tuple(
    record: dict[str, object],
    field: str,
    *,
    require_nonempty: bool = False,
) -> tuple[str, ...]:
    value = record[field]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise BlueprintCatalogError(f"Blueprint field {field} must be a string array.")
    values = tuple(value)
    if require_nonempty and not values:
        raise BlueprintCatalogError(f"Blueprint field {field} cannot be empty.")
    if len(values) != len(set(values)):
        raise BlueprintCatalogError(f"Blueprint field {field} contains duplicates.")
    if any(not _IDENTIFIER_PATTERN.fullmatch(item) for item in values):
        raise BlueprintCatalogError(f"Blueprint field {field} contains an invalid capability.")
    return tuple(sorted(values))


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as error:
        raise BlueprintCatalogError("Manifest data must contain only JSON values.") from error
