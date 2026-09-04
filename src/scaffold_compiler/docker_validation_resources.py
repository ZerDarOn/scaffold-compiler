"""Deterministic, opaque ownership identities for disposable Docker validation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final

_CANDIDATE_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_RESOURCE_PREFIX: Final = "scaffold-validation"
_OWNERSHIP_LABEL_NAME: Final = "io.scaffold-compiler.validation"
_TOKEN_LENGTH: Final = 16


@dataclass(frozen=True, slots=True)
class DockerValidationResources:
    """Exact Docker names and label shared by one validation lifecycle."""

    image_tag: str
    container_name: str
    compose_project: str
    ownership_label_name: str
    ownership_label_value: str

    @classmethod
    def create(cls, run_id: str, candidate_digest: str) -> DockerValidationResources:
        """Derive bounded Docker-safe names without exposing user-controlled identifiers."""
        if not run_id or len(run_id) > 128 or "\0" in run_id:
            raise ValueError("Run ID must contain between 1 and 128 non-NUL characters.")
        if not _CANDIDATE_DIGEST_PATTERN.fullmatch(candidate_digest):
            raise ValueError("Candidate digest must be a lowercase SHA-256 digest.")
        identity = hashlib.sha256(
            run_id.encode("utf-8") + b"\0" + candidate_digest.encode("ascii")
        ).hexdigest()[:_TOKEN_LENGTH]
        resource_name = f"{_RESOURCE_PREFIX}-{identity}"
        return cls(
            image_tag=f"{_RESOURCE_PREFIX}:{identity}",
            container_name=resource_name,
            compose_project=resource_name,
            ownership_label_name=_OWNERSHIP_LABEL_NAME,
            ownership_label_value=identity,
        )
