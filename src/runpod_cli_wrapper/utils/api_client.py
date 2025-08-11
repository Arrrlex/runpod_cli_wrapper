"""
RunPod API client wrapper with improved error handling.

This module provides a clean interface to the RunPod API with proper error
handling, type safety, and retry logic.
"""

import time
from typing import Any

import runpod

from runpod_cli_wrapper.core.models import PodStatus
from runpod_cli_wrapper.utils.errors import APIError, PodError


class RunPodAPIClient:
    """Wrapper around the RunPod SDK with enhanced error handling."""

    def __init__(self, api_key: str | None = None):
        """Initialize the client with an optional API key."""
        if api_key:
            runpod.api_key = api_key

    def get_pod(self, pod_id: str) -> dict[str, Any]:
        """Get pod details by ID with error handling."""
        try:
            pod_data = runpod.get_pod(pod_id)
            if not isinstance(pod_data, dict) or not pod_data.get("id"):
                raise APIError.invalid_response(f"Invalid pod data for {pod_id}")
            return pod_data
        except Exception as e:
            if "not found" in str(e).lower() or "does not exist" in str(e).lower():
                raise PodError.invalid_status(pod_id) from e
            raise APIError.connection_failed(str(e)) from e

    def get_pod_status(self, pod_id: str) -> PodStatus:
        """Get the status of a pod."""
        try:
            pod_data = self.get_pod(pod_id)
            desired_status = str(pod_data.get("desiredStatus", "")).upper()

            if desired_status == "RUNNING":
                return PodStatus.RUNNING
            elif desired_status == "EXITED":
                return PodStatus.STOPPED
            else:
                return PodStatus.INVALID

        except (PodError, APIError):
            return PodStatus.INVALID

    def create_pod(
        self,
        name: str,
        image_name: str,
        gpu_type_id: str,
        gpu_count: int,
        volume_in_gb: int,
        support_public_ip: bool = True,
        start_ssh: bool = True,
        ports: str = "22/tcp",
    ) -> dict[str, Any]:
        """Create a new pod with error handling."""
        try:
            result = runpod.create_pod(
                name=name,
                image_name=image_name,
                gpu_type_id=gpu_type_id,
                gpu_count=gpu_count,
                volume_in_gb=volume_in_gb,
                support_public_ip=support_public_ip,
                start_ssh=start_ssh,
                ports=ports,
            )

            if not isinstance(result, dict) or not result.get("id"):
                raise APIError.invalid_response(
                    "Could not determine created pod ID from response"
                )

            return result

        except Exception as e:
            if isinstance(e, APIError):
                raise
            raise PodError.creation_failed(str(e)) from e

    def start_pod(self, pod_id: str, gpu_count: int = 1) -> None:
        """Start/resume a pod."""
        try:
            runpod.resume_pod(pod_id, gpu_count=gpu_count)
        except Exception as e:
            # Check if pod is already running
            try:
                pod_data = self.get_pod(pod_id)
                if pod_data.get("desiredStatus") == "RUNNING":
                    return  # Already running, not an error
            except Exception:
                pass
            raise PodError.operation_failed("start", pod_id, str(e)) from e

    def stop_pod(self, pod_id: str) -> None:
        """Stop a pod."""
        try:
            runpod.stop_pod(pod_id)
        except Exception as e:
            # Check if pod is already stopped
            try:
                pod_data = self.get_pod(pod_id)
                if pod_data.get("desiredStatus") == "EXITED":
                    return  # Already stopped, not an error
            except Exception:
                pass
            raise PodError.operation_failed("stop", pod_id, str(e)) from e

    def terminate_pod(self, pod_id: str) -> None:
        """Terminate/destroy a pod."""
        try:
            runpod.terminate_pod(pod_id)
        except Exception as e:
            raise PodError.operation_failed("terminate", pod_id, str(e)) from e

    def wait_for_pod_ready(self, pod_id: str, timeout: int = 600) -> dict[str, Any]:
        """Wait for a pod to be ready with network information."""
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                pod_data = self.get_pod(pod_id)
                runtime = pod_data.get("runtime")
                if runtime is not None and isinstance(runtime, dict):
                    # Pod has network info, it's ready
                    return pod_data
            except PodError:
                # Pod not found or invalid, keep trying
                pass
            except APIError as e:
                # API error, re-raise immediately
                raise e

            time.sleep(5)

        raise PodError.timeout("become ready", timeout)

    def get_gpus(self) -> list[dict[str, Any]]:
        """Get available GPU types."""
        try:
            gpu_data = runpod.get_gpus()

            if isinstance(gpu_data, list):
                return gpu_data
            elif isinstance(gpu_data, dict) and "gpus" in gpu_data:
                return gpu_data["gpus"]
            else:
                raise APIError.invalid_response("Unexpected GPU data format")

        except Exception as e:
            if isinstance(e, APIError):
                raise
            raise APIError.connection_failed(str(e)) from e

    def find_gpu_type_id(self, model_key: str) -> str:
        """Find GPU type ID by model name, preferring highest VRAM."""
        gpus = self.get_gpus()
        model_upper = model_key.upper()
        candidates: list[tuple[float, str]] = []

        for gpu in gpus:
            gpu_id = str(gpu.get("id", ""))
            name = str(gpu.get("displayName", ""))
            memory = gpu.get("memoryInGb")

            if model_upper in gpu_id.upper() or model_upper in name.upper():
                try:
                    mem_val = float(memory) if memory is not None else 0.0
                except (ValueError, TypeError):
                    mem_val = 0.0
                candidates.append((mem_val, gpu_id))

        if not candidates:
            raise APIError.invalid_response(
                f"Could not find GPU type matching '{model_key}'. "
                "Try a different value (e.g., A100, H100, L40S)."
            )

        # Sort by memory (descending) and return the highest memory variant
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def extract_network_info(
        self, pod_data: dict[str, Any]
    ) -> tuple[str | None, int | None]:
        """Extract IP address and SSH port from pod data."""
        runtime = pod_data.get("runtime", {})
        if not isinstance(runtime, dict):
            return None, None

        ports = runtime.get("ports", [])
        for port in ports:
            if port.get("privatePort") == 22 and port.get("isIpPublic") is True:
                return port.get("ip"), port.get("publicPort")

        return None, None
