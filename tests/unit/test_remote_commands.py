"""
Unit tests for remote access commands (cursor and shell).

These tests verify the cursor and shell command functionality.
"""

from unittest.mock import MagicMock, patch

import pytest
import typer

from runpod_cli_wrapper.cli.commands import cursor_command, shell_command
from runpod_cli_wrapper.utils.errors import AliasError


class TestCursorCommand:
    """Test cursor command functionality."""

    @patch("runpod_cli_wrapper.cli.commands.get_pod_manager")
    @patch("subprocess.run")
    def test_cursor_command_default_path(self, mock_subprocess, mock_get_pod_manager):
        """Test cursor command with default workspace path."""
        # Setup mock pod manager
        mock_manager = MagicMock()
        mock_manager.get_pod_id.return_value = "test-pod-id"
        mock_get_pod_manager.return_value = mock_manager

        # Setup mock subprocess
        mock_subprocess.return_value = MagicMock(returncode=0)

        # Run command
        cursor_command("test-alias")

        # Verify pod manager was called
        mock_manager.get_pod_id.assert_called_once_with("test-alias")

        # Verify subprocess was called with correct arguments
        mock_subprocess.assert_called_once_with(
            [
                "cursor",
                "--folder-uri",
                "vscode-remote://ssh-remote+test-alias/workspace",
            ],
            check=True,
        )

    @patch("runpod_cli_wrapper.cli.commands.get_pod_manager")
    @patch("subprocess.run")
    def test_cursor_command_custom_path(self, mock_subprocess, mock_get_pod_manager):
        """Test cursor command with custom path."""
        # Setup mock pod manager
        mock_manager = MagicMock()
        mock_manager.get_pod_id.return_value = "test-pod-id"
        mock_get_pod_manager.return_value = mock_manager

        # Setup mock subprocess
        mock_subprocess.return_value = MagicMock(returncode=0)

        # Run command with custom path
        cursor_command("test-alias", "/custom/path")

        # Verify subprocess was called with custom path
        mock_subprocess.assert_called_once_with(
            [
                "cursor",
                "--folder-uri",
                "vscode-remote://ssh-remote+test-alias/custom/path",
            ],
            check=True,
        )

    @patch("runpod_cli_wrapper.cli.commands.get_pod_manager")
    @patch("subprocess.run")
    def test_cursor_command_cursor_not_found(
        self, mock_subprocess, mock_get_pod_manager
    ):
        """Test cursor command when cursor executable is not found."""
        # Setup mock pod manager
        mock_manager = MagicMock()
        mock_manager.get_pod_id.return_value = "test-pod-id"
        mock_get_pod_manager.return_value = mock_manager

        # Setup mock subprocess to raise FileNotFoundError
        mock_subprocess.side_effect = FileNotFoundError("cursor not found")

        # Run command and expect exit
        with pytest.raises(typer.Exit):
            cursor_command("test-alias")

    @patch("runpod_cli_wrapper.cli.commands.get_pod_manager")
    def test_cursor_command_invalid_alias(self, mock_get_pod_manager):
        """Test cursor command with invalid alias."""
        # Setup mock pod manager to raise error
        mock_manager = MagicMock()
        mock_manager.get_pod_id.side_effect = AliasError.not_found("invalid-alias")
        mock_get_pod_manager.return_value = mock_manager

        # Run command and expect typer.Exit (handle_cli_error converts to exit)
        with pytest.raises(typer.Exit):
            cursor_command("invalid-alias")


class TestShellCommand:
    """Test shell command functionality."""

    @patch("runpod_cli_wrapper.cli.commands.get_pod_manager")
    @patch("subprocess.run")
    def test_shell_command_success(self, mock_subprocess, mock_get_pod_manager):
        """Test shell command with successful connection."""
        # Setup mock pod manager
        mock_manager = MagicMock()
        mock_manager.get_pod_id.return_value = "test-pod-id"
        mock_get_pod_manager.return_value = mock_manager

        # Setup mock subprocess
        mock_subprocess.return_value = MagicMock(returncode=0)

        # Run command
        shell_command("test-alias")

        # Verify pod manager was called
        mock_manager.get_pod_id.assert_called_once_with("test-alias")

        # Verify subprocess was called with correct arguments
        mock_subprocess.assert_called_once_with(
            ["ssh", "-A", "test-alias"], check=False
        )

    @patch("runpod_cli_wrapper.cli.commands.get_pod_manager")
    @patch("subprocess.run")
    def test_shell_command_connection_closed(
        self, mock_subprocess, mock_get_pod_manager
    ):
        """Test shell command when connection is closed by user."""
        # Setup mock pod manager
        mock_manager = MagicMock()
        mock_manager.get_pod_id.return_value = "test-pod-id"
        mock_get_pod_manager.return_value = mock_manager

        # Setup mock subprocess to simulate user exit (non-zero but expected)
        mock_subprocess.return_value = MagicMock(returncode=130)  # SIGINT

        # Run command - should not raise exception
        shell_command("test-alias")

        # Verify subprocess was called
        mock_subprocess.assert_called_once_with(
            ["ssh", "-A", "test-alias"], check=False
        )

    @patch("runpod_cli_wrapper.cli.commands.get_pod_manager")
    def test_shell_command_invalid_alias(self, mock_get_pod_manager):
        """Test shell command with invalid alias."""
        # Setup mock pod manager to raise error
        mock_manager = MagicMock()
        mock_manager.get_pod_id.side_effect = AliasError.not_found("invalid-alias")
        mock_get_pod_manager.return_value = mock_manager

        # Run command and expect typer.Exit (handle_cli_error converts to exit)
        with pytest.raises(typer.Exit):
            shell_command("invalid-alias")
