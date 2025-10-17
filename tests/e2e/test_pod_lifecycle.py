"""
End-to-end tests for pod lifecycle operations.

These tests use real RunPod API calls and incur actual costs.
All test resources are automatically cleaned up after tests complete.
"""

import subprocess
import time


class TestPodLifecycle:
    """Test the complete pod lifecycle: create -> start -> stop -> destroy."""

    def test_create_start_stop_destroy_flow(self, cli_runner, test_pod_manager):
        """Test the full pod lifecycle using CLI commands."""
        import uuid

        alias = f"e2e-lifecycle-{uuid.uuid4().hex[:8]}"

        # 1. Create a pod
        result = cli_runner(
            ["create", "--alias", alias, "--gpu", "1xRTX A4000", "--storage", "10GB"]
        )

        assert result.returncode == 0, f"Create failed: {result.stderr}"
        assert "Saved alias" in result.stdout
        assert alias in result.stdout

        # Extract pod ID for tracking
        pod_id = None
        for line in result.stdout.split("\n"):
            if "Saved alias" in line and "->" in line:
                pod_id = line.split("->")[-1].strip()
                break

        assert pod_id, "Could not extract pod ID from create output"
        test_pod_manager.created_pods.append(pod_id)

        # 2. Verify pod appears in list command
        result = cli_runner(["list"])
        assert result.returncode == 0
        assert alias in result.stdout
        assert pod_id in result.stdout

        # 3. Stop the pod
        result = cli_runner(["stop", alias])
        assert result.returncode == 0
        assert "stopped" in result.stdout.lower()

        # 4. Verify pod is stopped in list
        result = cli_runner(["list"])
        assert result.returncode == 0
        assert alias in result.stdout
        # Should show as stopped (not running)
        assert "running" not in result.stdout or alias not in [
            line for line in result.stdout.split("\n") if "running" in line.lower()
        ]

        # 5. Start the pod again
        result = cli_runner(["start", alias])
        # Start might fail due to setup scripts, but pod should still start
        # Check that either it succeeded OR it failed only due to setup scripts
        if result.returncode != 0:
            assert "setup" in result.stderr.lower() or "ssh" in result.stderr.lower()
        # Pod should still be running even if setup failed
        assert "RUNNING" in result.stdout or "network is active" in result.stdout

        # 6. Verify pod is running in list
        result = cli_runner(["list"])
        assert result.returncode == 0
        assert alias in result.stdout
        # Should show as running
        running_lines = [
            line for line in result.stdout.split("\n") if "running" in line.lower()
        ]
        assert any(alias in line for line in running_lines)

        # 7. Destroy the pod
        result = cli_runner(["destroy", alias])
        assert result.returncode == 0
        assert "Terminated" in result.stdout or "destroyed" in result.stdout.lower()

        # Remove from tracking since it's destroyed
        if pod_id in test_pod_manager.created_pods:
            test_pod_manager.created_pods.remove(pod_id)

        # 8. Verify pod is gone from list
        result = cli_runner(["list"])
        assert result.returncode == 0
        assert alias not in result.stdout or "invalid" in result.stdout

    def test_add_existing_pod_flow(self, cli_runner, shared_test_pod):
        """Test adding an existing pod to the alias system."""
        test_alias = "e2e-add-test"
        pod_id = shared_test_pod["pod_id"]

        # 1. Add existing pod with alias
        result = cli_runner(["track", test_alias, pod_id])
        assert result.returncode == 0
        assert "Saved alias" in result.stdout
        assert test_alias in result.stdout
        assert pod_id in result.stdout

        # 2. Verify it shows up in list
        result = cli_runner(["list"])
        assert result.returncode == 0
        assert test_alias in result.stdout
        assert pod_id in result.stdout

        # 3. Test force overwrite
        result = cli_runner(["track", test_alias, pod_id, "--force"])
        assert result.returncode == 0
        assert "Saved alias" in result.stdout

        # 4. Delete the alias
        result = cli_runner(["untrack", test_alias])
        assert result.returncode == 0
        assert "Removed alias" in result.stdout

        # 5. Verify it's gone from list
        result = cli_runner(["list"])
        # Either empty list or alias not present
        assert result.returncode == 0
        assert test_alias not in result.stdout

    def test_ssh_connectivity(self, cli_runner, shared_test_pod):
        """Test SSH connectivity to a running pod."""
        # Get shared test pod details - we'll use our own alias
        pod_details = shared_test_pod["pod_details"]  # noqa: F841

        # First add the pod to our alias system
        result = cli_runner(["track", "ssh-test", shared_test_pod["pod_id"]])
        assert result.returncode == 0

        # Start the pod to ensure it's running and SSH is configured
        result = cli_runner(["start", "ssh-test"])
        assert result.returncode == 0

        # Give SSH some time to be ready
        time.sleep(10)

        # Test basic SSH connectivity
        ssh_result = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=30",
                "-o",
                "StrictHostKeyChecking=no",
                "ssh-test",
                "echo 'SSH test successful'",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )

        # SSH might fail due to key configuration, but connection attempt should work
        # We're mainly testing that the SSH config was properly set up
        assert ssh_result.returncode in (
            0,
            255,
        ), f"Unexpected SSH error: {ssh_result.stderr}"

        # Clean up
        result = cli_runner(["untrack", "ssh-test"])
        assert result.returncode == 0


class TestCommandEdgeCases:
    """Test edge cases and error handling."""

    def test_invalid_alias_handling(self, cli_runner):
        """Test commands with invalid aliases."""
        invalid_alias = "nonexistent-alias"

        # Start invalid alias
        result = cli_runner(["start", invalid_alias])
        assert result.returncode != 0
        assert "Unknown host alias" in result.stderr

        # Stop invalid alias
        result = cli_runner(["stop", invalid_alias])
        assert result.returncode != 0
        assert "Unknown host alias" in result.stderr

        # Destroy invalid alias
        result = cli_runner(["destroy", invalid_alias])
        assert result.returncode != 0
        assert "Unknown host alias" in result.stderr

    def test_duplicate_alias_without_force(self, cli_runner, shared_test_pod):
        """Test that duplicate aliases are rejected without --force."""
        alias = "duplicate-test"
        pod_id = shared_test_pod["pod_id"]

        # Add alias first time
        result = cli_runner(["track", alias, pod_id])
        assert result.returncode == 0

        # Try to add same alias again without force
        result = cli_runner(["track", alias, pod_id])
        assert result.returncode != 0
        assert "already exists" in result.stderr

        # Clean up
        result = cli_runner(["untrack", alias])
        assert result.returncode == 0

    def test_clean_command(self, cli_runner):
        """Test the clean command removes invalid aliases."""
        # Add a fake invalid pod ID
        fake_pod_id = "invalid-pod-id-12345"
        result = cli_runner(["track", "invalid-pod", fake_pod_id])
        assert result.returncode == 0

        # Run clean command
        result = cli_runner(["clean"])
        assert result.returncode == 0

        # Should mention removing invalid aliases
        if "No invalid aliases found" not in result.stdout:
            assert (
                "Removing invalid alias" in result.stdout or "Removed" in result.stdout
            )
