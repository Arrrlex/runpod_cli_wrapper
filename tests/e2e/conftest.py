"""
E2E test specific configuration and fixtures.

These fixtures are only used by E2E tests that interact with real RunPod API.
"""

import pytest


@pytest.fixture(scope="module", autouse=True)
def e2e_confirmation(confirm_e2e_tests):
    """Auto-run confirmation for all E2E tests in this module."""
    pass


@pytest.fixture(scope="module")
def shared_test_pod(test_pod_manager):
    """Create a shared test pod for E2E tests to reduce costs."""
    alias = "e2e-shared-pod"
    pod = test_pod_manager.create_test_pod(alias)
    pod_id = pod["id"]

    # Wait for pod to be ready
    pod_details = test_pod_manager.wait_for_pod_ready(pod_id)

    return {
        "alias": alias,
        "pod_id": pod_id,
        "pod_details": pod_details,
    }
