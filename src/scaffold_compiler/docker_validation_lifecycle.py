"""Compensating lifecycle control for disposable Docker validation resources."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Final

from scaffold_compiler.docker_validation_commands import DockerValidationCommands
from scaffold_compiler.validation import (
    ControlledProcessResult,
    ControlledProcessSpec,
    ValidationCheck,
    ValidationPhase,
    ValidationStatus,
    check_from_process_result,
    run_controlled_process,
    skipped_validation_check,
)

LOGGER = logging.getLogger(__name__)

_HEALTH_POLL_ATTEMPTS: Final = 60
_HEALTH_POLL_INTERVAL_SECONDS: Final = 2.0
_IMAGE_VALIDATIONS: Final = (
    "docker-build",
    "container-non-root",
    "container-health",
)
_COMPOSE_VALIDATIONS: Final = (
    "compose-config",
    "compose-up",
    "compose-health",
    "compose-cleanup",
)

ProcessRunner = Callable[[ControlledProcessSpec], ControlledProcessResult]


def execute_docker_validation_lifecycle(
    commands: DockerValidationCommands,
    required_validations: tuple[str, ...],
    *,
    process_runner: ProcessRunner = run_controlled_process,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[ValidationCheck, ...]:
    """Run requested delivery gates with exact preflight and compensating cleanup."""
    requested = frozenset(required_validations)
    unknown = requested.difference((*_IMAGE_VALIDATIONS, *_COMPOSE_VALIDATIONS))
    if unknown:
        raise ValueError("Docker lifecycle received an unsupported validation name.")
    checks: list[ValidationCheck] = []

    def safe_runner(specification: ControlledProcessSpec) -> ControlledProcessResult:
        return _run_safely(process_runner, specification)

    LOGGER.info(
        "docker_validation_started validation_id=%s required=%d",
        commands.validation_id,
        len(requested),
    )
    defer_container_health = {
        "container-health",
        "compose-health",
    }.issubset(requested)
    image_requested = (
        requested.difference({"container-health"}) if defer_container_health else requested
    )
    if requested.intersection(_IMAGE_VALIDATIONS):
        checks.extend(_execute_image_lifecycle(commands, image_requested, safe_runner, sleep))
    if requested.intersection(_COMPOSE_VALIDATIONS):
        compose_checks = _execute_compose_lifecycle(commands, requested, safe_runner)
        if defer_container_health:
            container_health = _container_health_from_compose(compose_checks)
            cleanup_index = next(
                (index for index, check in enumerate(checks) if check.name == "docker-cleanup"),
                len(checks),
            )
            checks.insert(cleanup_index, container_health)
        checks.extend(compose_checks)
    LOGGER.info(
        "docker_validation_completed validation_id=%s checks=%d incomplete=%d",
        commands.validation_id,
        len(checks),
        sum(check.status is not ValidationStatus.PASS for check in checks),
    )
    return tuple(checks)


def _container_health_from_compose(
    compose_checks: list[ValidationCheck],
) -> ValidationCheck:
    compose_health = next(check for check in compose_checks if check.name == "compose-health")
    diagnostics = (
        "Application container health was verified by the complete Compose stack."
        if compose_health.status is ValidationStatus.PASS
        else "Application container health requires a passing Compose health gate."
    )
    return ValidationCheck(
        name="container-health",
        status=compose_health.status,
        required=True,
        phase=ValidationPhase.DELIVERY,
        diagnostics=diagnostics,
    )


def _execute_image_lifecycle(
    commands: DockerValidationCommands,
    requested: frozenset[str],
    process_runner: ProcessRunner,
    sleep: Callable[[float], None],
) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    preflight = (
        process_runner(commands.inspect_image_absence()),
        process_runner(commands.inspect_container_absence()),
    )
    if not _resources_absent(preflight):
        LOGGER.warning(
            "docker_validation_preflight_refused validation_id=%s scope=image",
            commands.validation_id,
        )
        return [
            _failed_check("docker-build", "Docker resource preflight did not pass."),
            *_skipped_requested(
                requested,
                ("container-non-root", "container-health"),
                "Docker resource preflight did not pass.",
            ),
            _passed_check("docker-cleanup", "No validation resource was created."),
        ]

    image_attempted = False
    container_attempted = False
    try:
        image_attempted = True
        build_result = process_runner(commands.build_image())
        build = check_from_process_result(
            "docker-build", ValidationPhase.DELIVERY, required=True, result=build_result
        )
        if build.status is ValidationStatus.PASS and not _owned_resource(
            process_runner(commands.inspect_image_ownership()), commands.validation_id
        ):
            build = _failed_check("docker-build", "Built image ownership could not be verified.")
        checks.append(build)
        if build.status is not ValidationStatus.PASS:
            checks.extend(
                _skipped_requested(
                    requested,
                    ("container-non-root", "container-health"),
                    "Docker image build did not pass.",
                )
            )
            return checks

        if "container-non-root" in requested:
            non_root = check_from_process_result(
                "container-non-root",
                ValidationPhase.DELIVERY,
                required=True,
                result=process_runner(commands.check_non_root()),
            )
            checks.append(non_root)
            if non_root.status is not ValidationStatus.PASS:
                checks.extend(
                    _skipped_requested(
                        requested,
                        ("container-health",),
                        "Container user validation did not pass.",
                    )
                )
                return checks

        if "container-health" in requested:
            container_attempted = True
            started = process_runner(commands.start_health_container())
            if started.return_code != 0 or started.timed_out:
                checks.append(
                    check_from_process_result(
                        "container-health",
                        ValidationPhase.DELIVERY,
                        required=True,
                        result=started,
                    )
                )
            else:
                ownership = process_runner(commands.inspect_container_ownership())
                if not _owned_resource(ownership, commands.validation_id):
                    checks.append(
                        _failed_check(
                            "container-health",
                            "Container ownership could not be verified.",
                        )
                    )
                else:
                    checks.append(_poll_container_health(commands, process_runner, sleep))
        return checks
    finally:
        cleanup_results: list[ControlledProcessResult] = []
        if container_attempted and _owned_resource(
            process_runner(commands.inspect_container_ownership()), commands.validation_id
        ):
            cleanup_results.append(process_runner(commands.remove_container()))
        if image_attempted and _owned_resource(
            process_runner(commands.inspect_image_ownership()), commands.validation_id
        ):
            cleanup_results.append(process_runner(commands.remove_image()))
        residue = (
            process_runner(commands.inspect_container_absence()),
            process_runner(commands.inspect_image_absence()),
        )
        cleanup_passed = _resources_absent(residue)
        checks.append(
            _passed_check("docker-cleanup")
            if cleanup_passed
            else _failed_check("docker-cleanup", "Docker resource cleanup is incomplete.")
        )
        LOGGER.info(
            "docker_validation_cleanup validation_id=%s scope=image commands=%d completed=%s",
            commands.validation_id,
            len(cleanup_results),
            cleanup_passed,
        )


def _execute_compose_lifecycle(
    commands: DockerValidationCommands,
    requested: frozenset[str],
    process_runner: ProcessRunner,
) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    preflight = process_runner(commands.inspect_compose_absence())
    if not _resources_absent((preflight,), json_output=True):
        return [
            _failed_check("compose-config", "Compose resource preflight did not pass."),
            *_skipped_requested(
                requested,
                ("compose-up", "compose-health"),
                "Compose resource preflight did not pass.",
            ),
            _passed_check("compose-cleanup", "No validation resource was created."),
        ]

    up_attempted = False
    try:
        config = check_from_process_result(
            "compose-config",
            ValidationPhase.DELIVERY,
            required=True,
            result=process_runner(commands.compose_config()),
        )
        checks.append(config)
        if config.status is not ValidationStatus.PASS:
            checks.extend(
                _skipped_requested(
                    requested,
                    ("compose-up", "compose-health"),
                    "Compose configuration validation did not pass.",
                )
            )
            return checks

        up_attempted = True
        up = check_from_process_result(
            "compose-up",
            ValidationPhase.DELIVERY,
            required=True,
            result=process_runner(commands.compose_up()),
        )
        checks.append(up)
        if up.status is not ValidationStatus.PASS:
            checks.extend(
                _skipped_requested(
                    requested,
                    ("compose-health",),
                    "Compose startup did not pass.",
                )
            )
            return checks

        health_result = process_runner(commands.compose_health())
        services = frozenset(health_result.stdout.splitlines())
        if (
            health_result.return_code == 0
            and not health_result.timed_out
            and {"application", "database"}.issubset(services)
        ):
            checks.append(_passed_check("compose-health"))
        else:
            checks.append(_failed_check("compose-health", "Compose services are not healthy."))
        return checks
    finally:
        if up_attempted:
            process_runner(commands.compose_down())
        residue = process_runner(commands.inspect_compose_absence())
        cleanup_passed = _resources_absent((residue,), json_output=True)
        checks.append(
            _passed_check("compose-cleanup")
            if cleanup_passed
            else _failed_check("compose-cleanup", "Compose resource cleanup is incomplete.")
        )
        LOGGER.info(
            "docker_validation_cleanup validation_id=%s scope=compose completed=%s",
            commands.validation_id,
            cleanup_passed,
        )


def _poll_container_health(
    commands: DockerValidationCommands,
    process_runner: ProcessRunner,
    sleep: Callable[[float], None],
) -> ValidationCheck:
    for attempt in range(_HEALTH_POLL_ATTEMPTS):
        inspected = process_runner(commands.inspect_container_health())
        if inspected.return_code != 0 or inspected.timed_out:
            return _failed_check("container-health", "Container health inspection failed.")
        health = inspected.stdout.strip().strip('"')
        if health == "healthy":
            return _passed_check("container-health")
        if health == "unhealthy":
            return _failed_check("container-health", "Container reported unhealthy.")
        if attempt + 1 < _HEALTH_POLL_ATTEMPTS:
            sleep(_HEALTH_POLL_INTERVAL_SECONDS)
    return _failed_check("container-health", "Container health validation timed out.")


def _resources_absent(
    results: tuple[ControlledProcessResult, ...],
    *,
    json_output: bool = False,
) -> bool:
    if any(result.return_code != 0 or result.timed_out for result in results):
        return False
    for result in results:
        output = result.stdout.strip()
        if json_output:
            if output:
                try:
                    if json.loads(output):
                        return False
                except json.JSONDecodeError:
                    return False
        elif output:
            return False
    return True


def _owned_resource(result: ControlledProcessResult, validation_id: str) -> bool:
    return (
        result.return_code == 0 and not result.timed_out and result.stdout.strip() == validation_id
    )


def _run_safely(
    process_runner: ProcessRunner,
    specification: ControlledProcessSpec,
) -> ControlledProcessResult:
    try:
        return process_runner(specification)
    except OSError as error:
        LOGGER.error(
            "docker_validation_process_start_failed name=%s error_type=%s",
            specification.name,
            type(error).__name__,
        )
        return ControlledProcessResult(127, False, "", "", False, 0)


def _skipped_requested(
    requested: frozenset[str],
    ordered_names: tuple[str, ...],
    reason: str,
) -> list[ValidationCheck]:
    return [
        skipped_validation_check(
            name,
            ValidationPhase.DELIVERY,
            required=True,
            reason=reason,
        )
        for name in ordered_names
        if name in requested
    ]


def _passed_check(name: str, diagnostics: str = "") -> ValidationCheck:
    return ValidationCheck(
        name,
        ValidationStatus.PASS,
        required=True,
        phase=ValidationPhase.CLEANUP if name.endswith("cleanup") else ValidationPhase.DELIVERY,
        diagnostics=diagnostics,
    )


def _failed_check(name: str, diagnostics: str) -> ValidationCheck:
    return ValidationCheck(
        name,
        ValidationStatus.FAIL,
        required=True,
        phase=ValidationPhase.CLEANUP if name.endswith("cleanup") else ValidationPhase.DELIVERY,
        diagnostics=diagnostics,
    )
