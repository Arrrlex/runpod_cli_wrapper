"""
Pod management service for the RunPod CLI wrapper.

This module provides high-level operations for managing RunPod instances,
including creation, lifecycle management, and status tracking.
"""

import json

from runpod_cli_wrapper.config import POD_CONFIG_FILE, ensure_config_dir_exists
from runpod_cli_wrapper.core.models import Pod, PodCreateRequest, PodStatus
from runpod_cli_wrapper.utils.api_client import RunPodAPIClient
from runpod_cli_wrapper.utils.errors import AliasError, PodError


class PodManager:
    """Service for managing RunPod instances and their aliases."""

    def __init__(self, api_client: RunPodAPIClient | None = None):
        """Initialize the pod manager with an optional API client."""
        self.api_client = api_client or RunPodAPIClient()
        self._aliases: dict[str, str] | None = None

    @property
    def aliases(self) -> dict[str, str]:
        """Get current alias mappings, loading from disk if needed."""
        if self._aliases is None:
            self._aliases = self._load_aliases()
        return self._aliases

    def _load_aliases(self) -> dict[str, str]:
        """Load alias → pod_id mappings from storage."""
        try:
            with POD_CONFIG_FILE.open("r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
                return {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_aliases(self) -> None:
        """Save alias mappings to storage."""
        ensure_config_dir_exists()
        with POD_CONFIG_FILE.open("w") as f:
            json.dump(self.aliases, f, indent=2, sort_keys=True)
            f.write("\n")

    def add_alias(self, alias: str, pod_id: str, force: bool = False) -> None:
        """Add or update an alias mapping."""
        if alias in self.aliases and not force:
            raise AliasError.already_exists(alias)

        self.aliases[alias] = pod_id
        self._save_aliases()

    def remove_alias(self, alias: str, missing_ok: bool = False) -> str:
        """Remove an alias mapping, returning the pod ID."""
        if alias not in self.aliases:
            if missing_ok:
                return ""
            available = list(self.aliases.keys())
            raise AliasError.not_found(alias, available)

        pod_id = self.aliases.pop(alias)
        self._save_aliases()
        return pod_id

    def get_pod_id(self, alias: str) -> str:
        """Get pod ID for an alias, raising error if not found."""
        if alias not in self.aliases:
            available = list(self.aliases.keys())
            raise AliasError.not_found(alias, available)
        return self.aliases[alias]

    def get_pod(self, alias: str) -> Pod:
        """Get a Pod object for an alias."""
        pod_id = self.get_pod_id(alias)

        try:
            pod_data = self.api_client.get_pod(pod_id)
            return Pod.from_runpod_response(alias, pod_data)
        except PodError:
            # Pod is invalid but we have the alias mapping
            return Pod.from_alias_and_id(alias, pod_id, PodStatus.INVALID)

    def list_pods(self) -> list[Pod]:
        """List all managed pods with their current status."""
        pods = []
        for alias, pod_id in self.aliases.items():
            try:
                pod_data = self.api_client.get_pod(pod_id)
                pod = Pod.from_runpod_response(alias, pod_data)
            except (PodError, Exception):
                # Pod is invalid or inaccessible
                pod = Pod.from_alias_and_id(alias, pod_id, PodStatus.INVALID)
            pods.append(pod)

        return sorted(pods, key=lambda p: p.alias)

    def create_pod(self, request: PodCreateRequest) -> Pod:
        """Create a new pod according to the request specification."""
        # Check for existing alias
        if request.alias in self.aliases and not request.force:
            raise AliasError.already_exists(request.alias)

        if request.dry_run:
            # Return a mock pod for dry run
            return Pod.from_alias_and_id(
                request.alias, "dry-run-pod", PodStatus.STOPPED
            )

        # Resolve GPU type ID
        gpu_type_id = self.api_client.find_gpu_type_id(request.gpu_spec.model)

        # Create the pod
        created = self.api_client.create_pod(
            name=request.alias,
            image_name=request.image,
            gpu_type_id=gpu_type_id,
            gpu_count=request.gpu_spec.count,
            volume_in_gb=request.volume_gb,
            support_public_ip=True,
            start_ssh=True,
            ports=request.ports,
        )

        pod_id = created["id"]

        # Save the alias mapping
        self.aliases[request.alias] = pod_id
        self._save_aliases()

        # Wait for pod to be ready
        pod_data = self.api_client.wait_for_pod_ready(pod_id)

        return Pod.from_runpod_response(request.alias, pod_data)

    def start_pod(self, alias: str) -> Pod:
        """Start/resume a pod."""
        pod_id = self.get_pod_id(alias)

        self.api_client.start_pod(pod_id)

        # Wait for pod to be ready
        pod_data = self.api_client.wait_for_pod_ready(
            pod_id, timeout=120
        )  # 2 min timeout for start

        return Pod.from_runpod_response(alias, pod_data)

    def stop_pod(self, alias: str) -> None:
        """Stop a pod."""
        pod_id = self.get_pod_id(alias)
        self.api_client.stop_pod(pod_id)

    def destroy_pod(self, alias: str) -> str:
        """Destroy a pod and remove its alias, returning the pod ID."""
        pod_id = self.get_pod_id(alias)

        # Best-effort stop before termination
        try:
            status = self.api_client.get_pod_status(pod_id)
            if status == PodStatus.RUNNING:
                self.api_client.stop_pod(pod_id)
        except Exception:
            pass  # Ignore stop errors

        # Terminate the pod
        self.api_client.terminate_pod(pod_id)

        # Remove alias mapping
        self.remove_alias(alias, missing_ok=True)

        return pod_id

    def clean_invalid_aliases(self) -> int:
        """Remove aliases pointing to invalid/deleted pods."""
        invalid_aliases = []

        for alias, pod_id in list(self.aliases.items()):
            status = self.api_client.get_pod_status(pod_id)
            if status == PodStatus.INVALID:
                invalid_aliases.append(alias)

        for alias in invalid_aliases:
            self.remove_alias(alias, missing_ok=True)

        return len(invalid_aliases)

    def get_network_info(self, alias: str) -> tuple[str, int]:
        """Get IP address and SSH port for a pod."""
        pod = self.get_pod(alias)

        if not pod.ip_address or not pod.ssh_port:
            # Try to refresh pod data
            pod_data = self.api_client.get_pod(pod.id)
            ip, port = self.api_client.extract_network_info(pod_data)

            if not ip or not port:
                from runpod_cli_wrapper.utils.errors import SSHError

                raise SSHError.missing_network_info(pod.id)

            return ip, port

        return pod.ip_address, pod.ssh_port
