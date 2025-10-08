"""
Unit tests for Pydantic data models.

These tests verify model validation, serialization, and business logic.
"""

import pytest

from runpod_cli_wrapper.core.models import (
    AppConfig,
    GPUSpec,
    Pod,
    PodCreateRequest,
    PodStatus,
    PodTemplate,
    ScheduleTask,
    SSHConfig,
    TaskStatus,
)


class TestGPUSpec:
    """Test GPU specification model."""

    def test_valid_gpu_spec(self):
        """Test valid GPU specifications."""
        spec = GPUSpec(count=2, model="A100")
        assert spec.count == 2
        assert spec.model == "A100"
        assert str(spec) == "2xA100"

    def test_gpu_model_normalization(self):
        """Test GPU model is normalized to uppercase."""
        spec = GPUSpec(count=1, model="h100")
        assert spec.model == "H100"

    def test_invalid_gpu_count(self):
        """Test validation fails for invalid GPU count."""
        with pytest.raises(ValueError):
            GPUSpec(count=0, model="A100")

    def test_empty_model(self):
        """Test validation fails for empty model."""
        with pytest.raises(ValueError):
            GPUSpec(count=1, model="")


class TestPod:
    """Test Pod model."""

    def test_from_alias_and_id(self):
        """Test creating pod from alias and ID."""
        pod = Pod.from_alias_and_id("test-alias", "pod123")
        assert pod.alias == "test-alias"
        assert pod.id == "pod123"
        assert pod.status == PodStatus.INVALID

    def test_from_runpod_response_running(self):
        """Test creating pod from RunPod API response - running."""
        response = {
            "id": "pod123",
            "name": "test-pod",
            "desiredStatus": "RUNNING",
            "imageName": "pytorch:latest",
            "runtime": {
                "ports": [
                    {
                        "privatePort": 22,
                        "publicPort": 12345,
                        "ip": "1.2.3.4",
                        "isIpPublic": True,
                    }
                ]
            },
        }

        pod = Pod.from_runpod_response("test-alias", response)
        assert pod.alias == "test-alias"
        assert pod.id == "pod123"
        assert pod.status == PodStatus.RUNNING
        assert pod.ip_address == "1.2.3.4"
        assert pod.ssh_port == 12345

    def test_from_runpod_response_stopped(self):
        """Test creating pod from RunPod API response - stopped."""
        response = {
            "id": "pod123",
            "desiredStatus": "EXITED",
        }

        pod = Pod.from_runpod_response("test-alias", response)
        assert pod.status == PodStatus.STOPPED


class TestScheduleTask:
    """Test ScheduleTask model."""

    def test_schedule_task_creation(self):
        """Test creating a schedule task."""
        task = ScheduleTask(
            id="task123",
            action="stop",
            alias="test-pod",
            when_epoch=1642636800,  # 2022-01-20
            created_at="2022-01-19T12:00:00Z",
        )

        assert task.id == "task123"
        assert task.action == "stop"
        assert task.status == TaskStatus.PENDING

    def test_is_due(self):
        """Test task due checking."""
        task = ScheduleTask(
            id="task123",
            action="stop",
            alias="test-pod",
            when_epoch=1642636800,
            created_at="2022-01-19T12:00:00Z",
        )

        # Task is due if current time >= when_epoch
        assert task.is_due(1642636800)  # exactly due
        assert task.is_due(1642636801)  # past due
        assert not task.is_due(1642636799)  # not yet due

        # Failed/completed tasks are never due
        task.status = TaskStatus.FAILED
        assert not task.is_due(1642636801)


class TestSSHConfig:
    """Test SSH configuration model."""

    def test_ssh_config_creation(self):
        """Test creating SSH config."""
        config = SSHConfig(
            alias="test-pod", pod_id="pod123", hostname="1.2.3.4", port=12345
        )

        assert config.alias == "test-pod"
        assert config.hostname == "1.2.3.4"
        assert config.port == 12345
        assert config.user == "root"  # default

    def test_to_ssh_block(self):
        """Test generating SSH config block."""
        config = SSHConfig(
            alias="test-pod", pod_id="pod123", hostname="1.2.3.4", port=12345
        )

        block_lines = config.to_ssh_block("2022-01-20T12:00:00Z")

        # Convert to string for easier testing
        block_text = "".join(block_lines)

        assert "Host test-pod\n" in block_text
        assert "    HostName 1.2.3.4\n" in block_text  # Note the indentation
        assert "    Port 12345\n" in block_text
        assert "    User root\n" in block_text

        # Should contain marker with timestamp
        assert "rp:managed" in block_text
        assert "pod_id=pod123" in block_text
        assert "2022-01-20T12:00:00Z" in block_text

    def test_invalid_port(self):
        """Test port validation."""
        with pytest.raises(ValueError):
            SSHConfig(
                alias="test",
                pod_id="pod123",
                hostname="1.2.3.4",
                port=0,  # invalid port
            )


class TestPodCreateRequest:
    """Test pod creation request model."""

    def test_pod_create_request(self):
        """Test creating pod creation request."""
        gpu_spec = GPUSpec(count=1, model="A100")

        request = PodCreateRequest(alias="test-pod", gpu_spec=gpu_spec, volume_gb=100)

        assert request.alias == "test-pod"
        assert request.gpu_spec.count == 1
        assert request.volume_gb == 100
        assert not request.force  # default
        assert not request.dry_run  # default

    def test_minimum_storage_validation(self):
        """Test storage size validation."""
        gpu_spec = GPUSpec(count=1, model="A100")

        with pytest.raises(ValueError):
            PodCreateRequest(
                alias="test-pod",
                gpu_spec=gpu_spec,
                volume_gb=5,  # below minimum
            )


class TestPodTemplate:
    """Test pod template model."""

    def test_valid_template(self):
        """Test creating a valid pod template."""
        template = PodTemplate(
            identifier="alex-ast",
            alias_template="alex-ast-{i}",
            gpu_spec="2xA100",
            storage_spec="500GB",
        )

        assert template.identifier == "alex-ast"
        assert template.alias_template == "alex-ast-{i}"
        assert template.gpu_spec == "2xA100"
        assert template.storage_spec == "500GB"

    def test_missing_placeholder_validation(self):
        """Test validation fails when alias template is missing {i} placeholder."""
        with pytest.raises(
            ValueError, match="Alias template must contain '{i}' placeholder"
        ):
            PodTemplate(
                identifier="alex-ast",
                alias_template="alex-ast",  # missing {i}
                gpu_spec="2xA100",
                storage_spec="500GB",
            )

    def test_template_with_custom_image(self):
        """Test creating a template with a custom image."""
        custom_image = "runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel"
        template = PodTemplate(
            identifier="custom-image-template",
            alias_template="custom-{i}",
            gpu_spec="1xRTX4090",
            storage_spec="100GB",
            image=custom_image,
        )

        assert template.image == custom_image

    def test_template_default_image(self):
        """Test template without image uses None (will use default)."""
        template = PodTemplate(
            identifier="default-image",
            alias_template="default-{i}",
            gpu_spec="1xA100",
            storage_spec="200GB",
        )

        assert template.image is None


class TestAppConfig:
    """Test application configuration model."""

    def test_empty_config(self):
        """Test creating empty configuration."""
        config = AppConfig()

        assert config.aliases == {}
        assert config.scheduled_tasks == []
        assert config.pod_templates == {}

    def test_add_template(self):
        """Test adding a pod template."""
        config = AppConfig()
        template = PodTemplate(
            identifier="test-template",
            alias_template="test-{i}",
            gpu_spec="1xA100",
            storage_spec="100GB",
        )

        # Should succeed initially
        assert config.add_template(template)
        assert "test-template" in config.pod_templates

        # Should fail without force=True
        assert not config.add_template(template)

        # Should succeed with force=True
        assert config.add_template(template, force=True)

    def test_get_template(self):
        """Test retrieving a pod template."""
        config = AppConfig()
        template = PodTemplate(
            identifier="test-template",
            alias_template="test-{i}",
            gpu_spec="1xA100",
            storage_spec="100GB",
        )

        config.add_template(template)

        retrieved = config.get_template("test-template")
        assert retrieved is not None
        assert retrieved.identifier == "test-template"

        # Non-existent template
        assert config.get_template("nonexistent") is None

    def test_remove_template(self):
        """Test removing a pod template."""
        config = AppConfig()
        template = PodTemplate(
            identifier="test-template",
            alias_template="test-{i}",
            gpu_spec="1xA100",
            storage_spec="100GB",
        )

        config.add_template(template)

        # Should return the removed template
        removed = config.remove_template("test-template")
        assert removed is not None
        assert removed.identifier == "test-template"
        assert "test-template" not in config.pod_templates

        # Should return None for non-existent template
        assert config.remove_template("nonexistent") is None

    def test_find_next_alias_index(self):
        """Test finding the next available alias index."""
        config = AppConfig()

        # No existing aliases - should return 1
        assert config.find_next_alias_index("test-{i}") == 1

        # Add some aliases
        config.aliases["test-1"] = "pod1"
        config.aliases["test-3"] = "pod3"

        # Should return 2 (lowest available)
        assert config.find_next_alias_index("test-{i}") == 2

        # Add test-2
        config.aliases["test-2"] = "pod2"

        # Should now return 4
        assert config.find_next_alias_index("test-{i}") == 4

        # Test with different template format
        config.aliases["prefix-1-suffix"] = "pod4"
        assert config.find_next_alias_index("prefix-{i}-suffix") == 2


class TestPodConfig:
    """Test pod configuration model."""

    def test_empty_config(self):
        """Test creating empty pod config."""
        from runpod_cli_wrapper.core.models import PodConfig

        config = PodConfig()
        assert config.path is None

    def test_with_path(self):
        """Test creating pod config with path."""
        from runpod_cli_wrapper.core.models import PodConfig

        config = PodConfig(path="/workspace/my-project")
        assert config.path == "/workspace/my-project"


class TestPodMetadata:
    """Test pod metadata model."""

    def test_basic_metadata(self):
        """Test creating pod metadata with just ID."""
        from runpod_cli_wrapper.core.models import PodMetadata

        metadata = PodMetadata(pod_id="pod123")
        assert metadata.pod_id == "pod123"
        assert metadata.config.path is None

    def test_metadata_with_config(self):
        """Test creating pod metadata with config."""
        from runpod_cli_wrapper.core.models import PodConfig, PodMetadata

        metadata = PodMetadata(
            pod_id="pod123",
            config=PodConfig(path="/workspace/project"),
        )
        assert metadata.pod_id == "pod123"
        assert metadata.config.path == "/workspace/project"


class TestAppConfigMigration:
    """Test AppConfig legacy format migration."""

    def test_get_pod_config_value_from_new_format(self):
        """Test getting pod config value from new format."""
        config = AppConfig()
        config.add_alias("test-1", "pod123")
        config.set_pod_config_value("test-1", "path", "/workspace/project")

        value = config.get_pod_config("test-1")
        assert value is not None
        assert value.path == "/workspace/project"

    def test_set_pod_config_migrates_legacy(self):
        """Test that setting config migrates legacy alias."""
        config = AppConfig(aliases={"test-1": "pod123"})

        assert config.set_pod_config_value("test-1", "path", "/workspace/project")

        # Should migrate to new format
        assert "test-1" in config.pod_metadata
        assert "test-1" not in config.aliases
        assert config.pod_metadata["test-1"].config.path == "/workspace/project"

    def test_get_all_aliases_both_formats(self):
        """Test getting all aliases from both formats."""
        config = AppConfig(aliases={"legacy-1": "pod1"})
        config.add_alias("new-1", "pod2")

        all_aliases = config.get_all_aliases()
        assert all_aliases == {
            "legacy-1": "pod1",
            "new-1": "pod2",
        }
