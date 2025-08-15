"""
Unit tests for CLI utilities.

These tests verify CLI utility functions, parsing, and error handling.
"""

import pytest
import typer

from runpod_cli_wrapper.cli.utils import parse_gpu_spec, parse_storage_spec


class TestParseGPUSpec:
    """Test GPU specification parsing."""

    def test_parse_valid_gpu_specs(self):
        """Test parsing valid GPU specifications."""
        spec = parse_gpu_spec("2xA100")
        assert spec.count == 2
        assert spec.model == "A100"

        spec = parse_gpu_spec("1xH100")
        assert spec.count == 1
        assert spec.model == "H100"

    def test_parse_gpu_model_only(self):
        """Test parsing GPU model without count (defaults to 1)."""
        spec = parse_gpu_spec("h100")
        assert spec.count == 1
        assert spec.model == "H100"

        spec = parse_gpu_spec("A100")
        assert spec.count == 1
        assert spec.model == "A100"

        spec = parse_gpu_spec("RTX4090")
        assert spec.count == 1
        assert spec.model == "RTX4090"

    def test_parse_invalid_gpu_format(self):
        """Test parsing invalid GPU format."""
        with pytest.raises(typer.BadParameter):
            parse_gpu_spec("AxB100")  # non-numeric count

        with pytest.raises(typer.BadParameter):
            parse_gpu_spec("0xA100")  # zero count

    def test_parse_missing_model(self):
        """Test parsing with missing model."""
        with pytest.raises(typer.BadParameter):
            parse_gpu_spec("2x")

        # Test empty GPU model
        with pytest.raises(typer.BadParameter):
            parse_gpu_spec("")

        with pytest.raises(typer.BadParameter):
            parse_gpu_spec("   ")


class TestParseStorageSpec:
    """Test storage specification parsing."""

    def test_parse_gb_specs(self):
        """Test parsing GB specifications."""
        assert parse_storage_spec("100GB") == 100
        assert parse_storage_spec("500gb") == 500  # case insensitive
        assert parse_storage_spec("1000 GB") == 1000  # spaces handled

    def test_parse_tb_specs(self):
        """Test parsing TB specifications."""
        assert parse_storage_spec("1TB") == 1000
        assert parse_storage_spec("2TB") == 2000

    def test_parse_gib_specs(self):
        """Test parsing GiB specifications."""
        # GiB to GB conversion (approximate)
        result = parse_storage_spec("100GiB")
        assert 107 <= result <= 108  # ~107.4 GB

    def test_parse_invalid_format(self):
        """Test parsing invalid storage format."""
        with pytest.raises(typer.BadParameter):
            parse_storage_spec("100MB")  # unsupported unit

        with pytest.raises(typer.BadParameter):
            parse_storage_spec("invalid")

        with pytest.raises(typer.BadParameter):
            parse_storage_spec("5GB")  # below minimum

    def test_minimum_storage_validation(self):
        """Test minimum storage validation."""
        with pytest.raises(typer.BadParameter, match="at least 10GB"):
            parse_storage_spec("9GB")
