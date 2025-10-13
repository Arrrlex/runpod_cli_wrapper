"""
Pod management service for the RunPod CLI wrapper.

This module provides high-level operations for managing RunPod instances,
including creation, lifecycle management, and status tracking.
"""

import json

from rp.config import POD_CONFIG_FILE, ensure_config_dir_exists
from rp.core.models import (
    AppConfig,
    Pod,
    PodCreateRequest,
    PodStatus,
    PodTemplate,
)
from rp.utils.api_client import RunPodAPIClient
from rp.utils.errors import AliasError, PodError


class PodManager:
    """Service for managing RunPod instances and their aliases."""

    def __init__(self, api_client: RunPodAPIClient | None = None):
        """Initialize the pod manager with an optional API client."""
        self.api_client = api_client or RunPodAPIClient()
        self._config: AppConfig | None = None

    @property
    def config(self) -> AppConfig:
        """Get current configuration, loading from disk if needed."""
        if self._config is None:
            self._config = self._load_config()
        return self._config

    @property
    def aliases(self) -> dict[str, str]:
        """Get current alias mappings (from both legacy and new format)."""
        return self.config.get_all_aliases()

    def _load_config(self) -> AppConfig:
        """Load configuration from storage."""
        try:
            with POD_CONFIG_FILE.open("r") as f:
                data = json.load(f)
                # Handle legacy format (simple dict) and new format (AppConfig)
                if isinstance(data, dict):
                    if (
                        "aliases" in data
                        or "pod_templates" in data
                        or "scheduled_tasks" in data
                    ):
                        # New AppConfig format
                        return AppConfig.model_validate(data)
                    else:
                        # Legacy format - just aliases
                        return AppConfig(
                            aliases={str(k): str(v) for k, v in data.items()}
                        )
                return AppConfig()
        except (FileNotFoundError, json.JSONDecodeError):
            return AppConfig()

    def _save_config(self) -> None:
        """Save configuration to storage."""
        ensure_config_dir_exists()
        with POD_CONFIG_FILE.open("w") as f:
            f.write(self.config.model_dump_json(indent=2))
            f.write("\n")

    def add_alias(self, alias: str, pod_id: str, force: bool = False) -> None:
        """Add or update an alias mapping."""
        if not self.config.add_alias(alias, pod_id, force):
            raise AliasError.already_exists(alias)
        self._save_config()

    def remove_alias(self, alias: str, missing_ok: bool = False) -> str:
        """Remove an alias mapping, returning the pod ID."""
        pod_id = self.config.remove_alias(alias)
        if pod_id is None:
            if missing_ok:
                return ""
            available = list(self.aliases.keys())
            raise AliasError.not_found(alias, available)

        self._save_config()
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
            container_disk_in_gb=request.container_disk_gb,
            support_public_ip=True,
            start_ssh=True,
            ports=request.ports,
        )

        pod_id = created["id"]

        # Save the alias mapping
        self.config.add_alias(request.alias, pod_id, force=request.force)
        self._save_config()

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
                from rp.utils.errors import SSHError

                raise SSHError.missing_network_info(pod.id)

            return ip, port

        return pod.ip_address, pod.ssh_port

    # Template management methods
    def add_template(self, template: PodTemplate, force: bool = False) -> None:
        """Add or update a pod template."""
        if not self.config.add_template(template, force):
            raise AliasError.already_exists(template.identifier)
        self._save_config()

    def get_template(self, identifier: str) -> PodTemplate:
        """Get a pod template by identifier."""
        template = self.config.get_template(identifier)
        if template is None:
            available = list(self.config.pod_templates.keys())
            raise AliasError.not_found(identifier, available)
        return template

    def remove_template(
        self, identifier: str, missing_ok: bool = False
    ) -> PodTemplate | None:
        """Remove a template, return the template if it existed."""
        template = self.config.remove_template(identifier)
        if template is None and not missing_ok:
            available = list(self.config.pod_templates.keys())
            raise AliasError.not_found(identifier, available)
        if template is not None:
            self._save_config()
        return template

    def list_templates(self) -> list[PodTemplate]:
        """List all pod templates."""
        return sorted(self.config.pod_templates.values(), key=lambda t: t.identifier)

    def create_pod_from_template(
        self,
        template_identifier: str,
        force: bool = False,
        dry_run: bool = False,
        alias_override: str | None = None,
    ) -> Pod:
        """Create a pod using a template, finding the next available alias index or using provided alias."""
        template = self.get_template(template_identifier)

        # Use alias override if provided, otherwise find next available index
        if alias_override:
            alias = alias_override
        else:
            next_index = self.config.find_next_alias_index(template.alias_template)
            alias = template.alias_template.format(i=next_index)

        # Create the pod request
        from rp.cli.utils import parse_gpu_spec, parse_storage_spec

        gpu_spec = parse_gpu_spec(template.gpu_spec)
        volume_gb = parse_storage_spec(template.storage_spec)

        request_kwargs = {
            "alias": alias,
            "gpu_spec": gpu_spec,
            "volume_gb": volume_gb,
            "force": force,
            "dry_run": dry_run,
        }

        # Add container disk if specified in template
        if template.container_disk_spec is not None:
            container_disk_gb = parse_storage_spec(template.container_disk_spec)
            request_kwargs["container_disk_gb"] = container_disk_gb

        # Add image if specified in template
        if template.image is not None:
            request_kwargs["image"] = template.image

        request = PodCreateRequest(**request_kwargs)  # type: ignore[arg-type]

        return self.create_pod(request)

    def set_pod_config(self, alias: str, key: str, value: str | None) -> None:
        """Set a configuration value for a pod."""
        if not self.config.set_pod_config_value(alias, key, value):
            available = list(self.aliases.keys())
            raise AliasError.not_found(alias, available)
        self._save_config()

    def get_pod_config_value(self, alias: str, key: str) -> str | None:
        """Get a configuration value for a pod."""
        pod_config = self.config.get_pod_config(alias)
        if pod_config is None:
            available = list(self.aliases.keys())
            raise AliasError.not_found(alias, available)

        if key == "path":
            return pod_config.path

        return None

    def get_pod_config(self, alias: str) -> dict[str, str | None]:
        """Get all configuration values for a pod."""
        pod_config = self.config.get_pod_config(alias)
        if pod_config is None:
            available = list(self.aliases.keys())
            raise AliasError.not_found(alias, available)

        return {"path": pod_config.path}
