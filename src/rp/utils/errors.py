"""
Custom exception classes for the RunPod CLI wrapper.

This module defines application-specific exceptions that provide better
error handling and context throughout the application.
"""


class RunPodCLIError(Exception):
    """Base exception for all RunPod CLI wrapper errors."""

    def __init__(self, message: str, details: str | None = None, exit_code: int = 1):
        super().__init__(message)
        self.message = message
        self.details = details
        self.exit_code = exit_code


class AliasError(RunPodCLIError):
    """Errors related to alias management."""

    @classmethod
    def not_found(
        cls, alias: str, available_aliases: list[str] | None = None
    ) -> "AliasError":
        """Create error for unknown alias."""
        message = f"Unknown host alias: {alias}"
        details = None
        if available_aliases:
            details = f"Available aliases: {', '.join(available_aliases)}"
        elif available_aliases is not None:  # Empty list
            details = "No aliases configured. Add one with: rp add <alias> <pod_id>"
        return cls(message, details)

    @classmethod
    def already_exists(cls, alias: str) -> "AliasError":
        """Create error for duplicate alias."""
        return cls(
            f"Alias '{alias}' already exists. Use --force to overwrite.",
            details="Use 'rp list' to see existing aliases.",
        )


class PodError(RunPodCLIError):
    """Errors related to pod operations."""

    @classmethod
    def invalid_status(cls, pod_id: str, alias: str | None = None) -> "PodError":
        """Create error for invalid pod status."""
        pod_ref = f"pod {pod_id}"
        if alias:
            pod_ref = f"pod {pod_id} (alias: {alias})"
        return cls(
            f"Invalid or inaccessible {pod_ref}",
            details="Pod may have been deleted or you may not have access to it.",
        )

    @classmethod
    def creation_failed(cls, reason: str) -> "PodError":
        """Create error for pod creation failure."""
        return cls("Failed to create pod", details=reason)

    @classmethod
    def operation_failed(cls, operation: str, pod_id: str, reason: str) -> "PodError":
        """Create error for general pod operation failure."""
        return cls(f"Failed to {operation} pod {pod_id}", details=reason)

    @classmethod
    def timeout(cls, operation: str, timeout_seconds: int) -> "PodError":
        """Create error for operation timeout."""
        return cls(
            f"Timed out waiting for pod to {operation}",
            details=f"Operation did not complete within {timeout_seconds} seconds.",
        )


class APIError(RunPodCLIError):
    """Errors related to RunPod API interactions."""

    @classmethod
    def connection_failed(cls, reason: str) -> "APIError":
        """Create error for API connection failure."""
        return cls("Failed to connect to RunPod API", details=reason)

    @classmethod
    def authentication_failed(cls) -> "APIError":
        """Create error for API authentication failure."""
        return cls(
            "RunPod API authentication failed",
            details="Check your API key in RUNPOD_API_KEY env var or ~/.config/rp/runpod_api_key",
        )

    @classmethod
    def invalid_response(cls, details: str) -> "APIError":
        """Create error for unexpected API response."""
        return cls("Received unexpected response from RunPod API", details=details)


class SchedulingError(RunPodCLIError):
    """Errors related to task scheduling."""

    @classmethod
    def invalid_time_format(cls, time_str: str, reason: str) -> "SchedulingError":
        """Create error for invalid time format."""
        return cls(f"Invalid time format: {time_str}", details=reason)

    @classmethod
    def task_not_found(cls, task_id: str) -> "SchedulingError":
        """Create error for unknown task ID."""
        return cls(
            f"Task not found: {task_id}",
            details="Use 'rp schedule list' to see available tasks.",
        )

    @classmethod
    def conflicting_options(cls, option1: str, option2: str) -> "SchedulingError":
        """Create error for conflicting scheduling options."""
        return cls(
            f"{option1} and {option2} are mutually exclusive",
            details="Please use only one scheduling option.",
        )


class SSHError(RunPodCLIError):
    """Errors related to SSH configuration."""

    @classmethod
    def config_update_failed(cls, reason: str) -> "SSHError":
        """Create error for SSH config update failure."""
        return cls("Failed to update SSH configuration", details=reason)

    @classmethod
    def missing_network_info(cls, pod_id: str) -> "SSHError":
        """Create error for missing pod network information."""
        return cls(
            f"Could not find public SSH port information for pod {pod_id}",
            details="Pod may not be fully ready yet or SSH is not enabled.",
        )


class SetupScriptError(RunPodCLIError):
    """Errors related to setup script execution."""

    @classmethod
    def local_script_failed(cls, exit_code: int, stderr: str) -> "SetupScriptError":
        """Create error for local setup script failure."""
        return cls(
            f"Local setup script failed with exit code {exit_code}",
            details=stderr.strip() if stderr else None,
            exit_code=exit_code,
        )

    @classmethod
    def remote_script_failed(cls, exit_code: int, stderr: str) -> "SetupScriptError":
        """Create error for remote setup script failure."""
        return cls(
            f"Remote setup script failed with exit code {exit_code}",
            details=stderr.strip() if stderr else None,
            exit_code=exit_code,
        )
