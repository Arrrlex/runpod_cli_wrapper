"""
Pydantic data models for the RunPod CLI wrapper.

This module defines the core data structures used throughout the application,
providing type safety, validation, and serialization capabilities.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class PodStatus(str, Enum):
    """Enumeration of possible pod statuses."""

    RUNNING = "running"
    STOPPED = "stopped"
    INVALID = "invalid"


class TaskStatus(str, Enum):
    """Enumeration of possible task statuses."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GPUSpec(BaseModel):
    """GPU specification for pod creation."""

    count: int = Field(ge=1, description="Number of GPUs")
    model: str = Field(description="GPU model (e.g., 'A100', 'H100')")

    @field_validator("model")
    @classmethod
    def validate_model(cls, v):
        if not v or not v.strip():
            raise ValueError("GPU model cannot be empty")
        return v.strip().upper()

    def __str__(self) -> str:
        return f"{self.count}x{self.model}"


class Pod(BaseModel):
    """Represents a RunPod instance with its metadata."""

    id: str = Field(description="RunPod instance ID")
    alias: str = Field(description="User-friendly alias for the pod")
    status: PodStatus = Field(description="Current status of the pod")
    name: str | None = Field(None, description="Pod name")
    image: str | None = Field(None, description="Docker image used")
    gpu_spec: GPUSpec | None = Field(None, description="GPU configuration")
    volume_gb: int | None = Field(None, description="Storage volume size in GB")

    # Network information (populated when pod is running)
    ip_address: str | None = Field(None, description="Public IP address")
    ssh_port: int | None = Field(None, description="SSH port number")

    # Timestamps
    created_at: datetime | None = Field(None, description="Pod creation time")
    updated_at: datetime | None = Field(None, description="Last update time")

    @classmethod
    def from_alias_and_id(
        cls, alias: str, pod_id: str, status: PodStatus = PodStatus.INVALID
    ) -> "Pod":
        """Create a Pod instance from alias and ID."""
        return cls(id=pod_id, alias=alias, status=status)

    @classmethod
    def from_runpod_response(cls, alias: str, pod_data: dict) -> "Pod":
        """Create a Pod instance from RunPod API response."""
        pod_id = pod_data.get("id", "")

        # Determine status
        desired_status = str(pod_data.get("desiredStatus", "")).upper()
        if desired_status == "RUNNING":
            status = PodStatus.RUNNING
        elif desired_status == "EXITED":
            status = PodStatus.STOPPED
        else:
            status = PodStatus.INVALID

        # Extract network info
        ip_address = None
        ssh_port = None
        runtime = pod_data.get("runtime", {})
        if runtime and isinstance(runtime, dict):
            ports = runtime.get("ports", [])
            for port in ports:
                if port.get("privatePort") == 22 and port.get("isIpPublic"):
                    ip_address = port.get("ip")
                    ssh_port = port.get("publicPort")
                    break

        return cls(
            id=pod_id,
            alias=alias,
            status=status,
            name=pod_data.get("name"),
            image=pod_data.get("imageName"),
            ip_address=ip_address,
            ssh_port=ssh_port,
        )


class ScheduleTask(BaseModel):
    """Represents a scheduled task."""

    id: str = Field(description="Unique task identifier")
    action: str = Field(description="Action to perform (e.g., 'stop')")
    alias: str = Field(description="Pod alias to act upon")
    when_epoch: int = Field(description="Unix timestamp when to execute")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="Task status")
    created_at: str = Field(description="ISO timestamp when task was created")
    last_error: str | None = Field(None, description="Last error message if failed")

    @property
    def when_datetime(self) -> datetime:
        """Get execution time as datetime object."""
        return datetime.fromtimestamp(self.when_epoch)

    def is_due(self, current_epoch: int | None = None) -> bool:
        """Check if task is due for execution."""
        if self.status != TaskStatus.PENDING:
            return False
        if current_epoch is None:
            import time

            current_epoch = int(time.time())
        return self.when_epoch <= current_epoch


class SSHConfig(BaseModel):
    """Represents SSH configuration for a pod."""

    alias: str = Field(description="Host alias")
    pod_id: str = Field(description="Associated pod ID")
    hostname: str = Field(description="SSH hostname/IP")
    port: int = Field(ge=1, le=65535, description="SSH port")
    user: str = Field(default="root", description="SSH username")
    identity_file: str = Field(default="~/.ssh/runpod", description="SSH key file path")

    def to_ssh_block(self, updated_timestamp: str) -> list[str]:
        """Generate SSH config block lines."""
        return [
            f"Host {self.alias}\n",
            f"    # rp:managed alias={self.alias} pod_id={self.pod_id} updated={updated_timestamp}\n",
            f"    HostName {self.hostname}\n",
            f"    User {self.user}\n",
            f"    Port {self.port}\n",
            "    IdentitiesOnly yes\n",
            f"    IdentityFile {self.identity_file}\n",
            "    ForwardAgent yes\n",
        ]


class PodCreateRequest(BaseModel):
    """Request model for creating a new pod."""

    alias: str = Field(description="Alias for the pod")
    gpu_spec: GPUSpec = Field(description="GPU specification")
    volume_gb: int = Field(ge=10, description="Storage volume size in GB")
    force: bool = Field(default=False, description="Overwrite existing alias")
    dry_run: bool = Field(default=False, description="Show actions without executing")

    # Pod configuration
    image: str = Field(
        default="runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04",
        description="Docker image to use",
    )
    ports: str = Field(default="22/tcp,8888/http", description="Port configuration")


class PodTemplate(BaseModel):
    """Template for creating pods with predefined configurations."""

    identifier: str = Field(description="Unique template identifier (e.g., 'alex-ast')")
    alias_template: str = Field(
        description="Alias template with {i} placeholder (e.g., 'alex-ast-{i}')"
    )
    gpu_spec: str = Field(description="GPU specification string (e.g., '2xA100')")
    storage_spec: str = Field(
        description="Storage specification string (e.g., '500GB')"
    )
    image: str | None = Field(
        default=None, description="Docker image to use (None uses default)"
    )

    @field_validator("alias_template")
    @classmethod
    def validate_alias_template(cls, v):
        if "{i}" not in v:
            raise ValueError("Alias template must contain '{i}' placeholder")
        return v


class PodConfig(BaseModel):
    """Per-pod configuration settings."""

    cursor_path: str | None = Field(None, description="Default path for Cursor editor")


class PodMetadata(BaseModel):
    """Pod metadata including ID and optional configuration."""

    pod_id: str = Field(description="RunPod instance ID")
    config: PodConfig = Field(
        default_factory=PodConfig, description="Pod configuration"
    )


class AppConfig(BaseModel):
    """Application configuration and state."""

    aliases: dict[str, str] = Field(
        default_factory=dict, description="Alias to pod ID mappings (legacy format)"
    )
    pod_metadata: dict[str, PodMetadata] = Field(
        default_factory=dict, description="Pod metadata by alias (new format)"
    )
    scheduled_tasks: list[ScheduleTask] = Field(
        default_factory=list, description="Scheduled tasks"
    )
    pod_templates: dict[str, PodTemplate] = Field(
        default_factory=dict, description="Pod templates by identifier"
    )

    def add_alias(self, alias: str, pod_id: str, force: bool = False) -> bool:
        """Add or update an alias mapping."""
        # Check both legacy and new format
        if (alias in self.aliases or alias in self.pod_metadata) and not force:
            return False

        # Migrate to new format
        if alias in self.aliases:
            del self.aliases[alias]

        self.pod_metadata[alias] = PodMetadata(pod_id=pod_id)
        return True

    def remove_alias(self, alias: str) -> str | None:
        """Remove an alias mapping, return the pod ID if it existed."""
        # Try new format first
        if alias in self.pod_metadata:
            metadata = self.pod_metadata.pop(alias)
            return metadata.pod_id
        # Fall back to legacy format
        return self.aliases.pop(alias, None)

    def get_pod_id(self, alias: str) -> str | None:
        """Get pod ID for an alias."""
        # Try new format first
        if alias in self.pod_metadata:
            return self.pod_metadata[alias].pod_id
        # Fall back to legacy format
        return self.aliases.get(alias)

    def get_pod_config(self, alias: str) -> PodConfig | None:
        """Get pod configuration for an alias."""
        if alias in self.pod_metadata:
            return self.pod_metadata[alias].config
        return None

    def set_pod_config_value(self, alias: str, key: str, value: str | None) -> bool:
        """Set a configuration value for a pod. Returns True if successful."""
        # Migrate from legacy format if needed
        if alias in self.aliases and alias not in self.pod_metadata:
            pod_id = self.aliases.pop(alias)
            self.pod_metadata[alias] = PodMetadata(pod_id=pod_id)

        if alias not in self.pod_metadata:
            return False

        # Set the config value
        if key == "cursor_path":
            self.pod_metadata[alias].config.cursor_path = value
            return True

        return False

    def get_all_aliases(self) -> dict[str, str]:
        """Get all alias->pod_id mappings from both formats."""
        result = dict(self.aliases)  # Legacy format
        result.update({alias: meta.pod_id for alias, meta in self.pod_metadata.items()})
        return result

    def add_task(self, task: ScheduleTask) -> None:
        """Add a scheduled task."""
        self.scheduled_tasks.append(task)

    def get_pending_tasks(self, current_epoch: int | None = None) -> list[ScheduleTask]:
        """Get tasks that are due for execution."""
        return [task for task in self.scheduled_tasks if task.is_due(current_epoch)]

    def clean_completed_tasks(self) -> int:
        """Remove completed and cancelled tasks and return count removed."""
        original_count = len(self.scheduled_tasks)
        self.scheduled_tasks = [
            t
            for t in self.scheduled_tasks
            if t.status not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
        ]
        return original_count - len(self.scheduled_tasks)

    def add_template(self, template: PodTemplate, force: bool = False) -> bool:
        """Add or update a pod template."""
        if template.identifier in self.pod_templates and not force:
            return False
        self.pod_templates[template.identifier] = template
        return True

    def get_template(self, identifier: str) -> PodTemplate | None:
        """Get a pod template by identifier."""
        return self.pod_templates.get(identifier)

    def remove_template(self, identifier: str) -> PodTemplate | None:
        """Remove a template, return the template if it existed."""
        return self.pod_templates.pop(identifier, None)

    def find_next_alias_index(self, alias_template: str) -> int:
        """Find the lowest i ≥ 1 where alias_template.format(i=i) doesn't exist."""
        all_aliases = self.get_all_aliases()
        i = 1
        while True:
            candidate_alias = alias_template.format(i=i)
            if candidate_alias not in all_aliases:
                return i
            i += 1
