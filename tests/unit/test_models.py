"""
Unit tests for Pydantic data models.

These tests verify model validation, serialization, and business logic.
"""

import pytest

from runpod_cli_wrapper.core.models import (
    GPUSpec,
    Pod,
    PodCreateRequest,
    PodStatus,
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
