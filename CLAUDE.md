# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`rp` is a CLI wrapper around the RunPod Python API for managing GPU pods. It provides pod lifecycle management (create/start/stop/destroy), alias system, template-based deployment, scheduled shutdowns, SSH config management, and setup script automation.

**Key documentation**: `docs.md` contains comprehensive technical documentation including all commands, configuration files, and internal behavior. Read this first for complete context.

## Development Commands

### Environment Setup

```bash
# Sync dependencies
uv sync

# Install in development mode
uv pip install -e .
```

### Testing

```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/unit/test_cli_utils.py

# Run specific test
uv run pytest tests/unit/test_cli_utils.py::test_parse_gpu_spec

# Run with coverage
uv run pytest --cov=rp --cov-report=html

# E2E tests (requires RUNPOD_API_KEY)
uv run pytest tests/e2e/
```

### Linting & Formatting

```bash
# Check code style
ruff check

# Auto-fix issues
ruff check --fix

# Format code
ruff format
```

### Running the Tool Locally

```bash
# Run from source
uv run rp --help

# After installation
rp --help
```

## Architecture

The codebase follows a layered architecture with clear separation of concerns:

### Layer Overview

1. **CLI Layer** (`src/rp/cli/`)
   - `main.py`: Typer-based CLI entry point, command routing
   - `commands.py`: Command implementations that orchestrate service layer
   - `utils.py`: CLI utilities (error handling, parsing, display)

2. **Service Layer** (`src/rp/core/`)
   - `pod_manager.py`: Pod CRUD operations, template management, per-pod config
   - `scheduler.py`: Task scheduling, time parsing, macOS launchd integration
   - `ssh_manager.py`: SSH config file manipulation (marker-based block management)

3. **Data Layer** (`src/rp/core/models.py`)
   - Pydantic models for type safety and validation
   - `AppConfig`: Application state with dual alias format (legacy dict + new PodMetadata)
   - `Pod`, `PodTemplate`, `ScheduleTask`, `SSHConfig`, etc.

4. **API Layer** (`src/rp/utils/api_client.py`)
   - `RunPodAPIClient`: Wrapper around runpod SDK with error handling
   - GPU type resolution: queries available GPUs, matches by substring, prefers highest VRAM

### Configuration Storage

All configuration stored in `~/.config/rp/`:
- `pods.json`: Aliases, templates, per-pod config (managed by `AppConfig` model)
- `schedule.json`: Scheduled tasks (managed by `Scheduler`)
- `runpod_api_key`: API key (optional, can use env var)
- `setup_remote.sh`: Script run on pod (optional)
- `setup_local.sh`: Script run locally with `$POD_HOST` env var (optional)

### Key Design Patterns

**Dual Alias Format**: `AppConfig` maintains backward compatibility with legacy dict format while migrating to `PodMetadata` for per-pod configuration. Methods like `get_all_aliases()` merge both formats.

**SSH Block Management**: `SSHManager` uses marker comments (`# rp:managed alias=... pod_id=...`) to identify managed blocks in `~/.ssh/config`, allowing safe updates without touching user configs.

**GPU Resolution**: Two-stage process:
1. Parse string (`[count]xmodel`) into `GPUSpec`
2. Resolve model substring to RunPod GPU type ID via API, preferring highest VRAM

**Scheduler**: On macOS, creates launchd agent that runs `rp scheduler-tick` every 60 seconds. Tasks stored in `schedule.json` with Unix epoch timestamps.

**Template Auto-numbering**: `find_next_alias_index()` finds lowest `i ≥ 1` where `template.format(i=i)` doesn't exist in aliases.

## Code Patterns

### Error Handling

Use custom error classes from `utils/errors.py`:
- `RunPodCLIError` base class with `message`, `details`, `exit_code`
- Specific errors: `AliasError`, `PodError`, `APIError`, `SSHError`, `SchedulingError`
- CLI commands catch all exceptions and call `handle_cli_error()` for consistent output

### Service Instantiation

Services use lazy singleton pattern via module-level functions in `cli/commands.py`:
```python
def get_pod_manager() -> PodManager:
    global _pod_manager
    if _pod_manager is None:
        api_client = setup_api_client()
        _pod_manager = PodManager(api_client)
    return _pod_manager
```

### Pydantic Models

All data classes use Pydantic for validation:
- Type annotations with `Field()` for validation rules
- Factory methods: `Pod.from_runpod_response()`, `Pod.from_alias_and_id()`
- Validators: `@field_validator` for custom validation logic

### Configuration Persistence

Both `PodManager` and `Scheduler` follow pattern:
1. Load config on first property access (`@property config`)
2. Mutating operations call `_save_config()` to atomically write JSON
3. Use `model_dump_json()` for serialization

## Testing Notes

### Test Structure

- `tests/unit/`: Unit tests for utilities, parsers, models
- `tests/e2e/`: End-to-end tests requiring real RunPod API (uses fixtures to create/destroy pods)
- `tests/conftest.py`: Shared fixtures including CLI runner with environment setup

### E2E Test Patterns

E2E tests use a shared pod fixture (`shared_test_pod`) to avoid creating pods for every test. Tests add aliases temporarily and clean up after themselves:

```python
def test_something(cli_runner, shared_test_pod):
    alias = "test-alias"
    pod_id = shared_test_pod["pod_id"]

    # Add alias
    result = cli_runner(["add", alias, pod_id])

    # ... test logic ...

    # Clean up
    result = cli_runner(["delete", alias])
```

## Important Constraints

- **Python 3.13+** required (uses modern type syntax: `dict[str, str]`, `str | None`)
- **macOS** for automatic scheduling (uses launchd; other platforms require manual `scheduler-tick`)
- **SSH config**: Assumes `~/.ssh/config` exists and is writable
- **API Key**: First run prompts and saves to `~/.config/rp/runpod_api_key` unless `RUNPOD_API_KEY` env var set

## Common Gotchas

1. **GPU Parsing**: `x` in model name is allowed (e.g., `rtx4090`). Only treated as count separator if prefix is numeric.

2. **AppConfig Migration**: When reading `pods.json`, check for both `aliases` (legacy) and `pod_metadata` (new). When writing config for first time, migrate legacy to new format.

3. **Scheduler on macOS**: `ensure_macos_scheduler_installed()` must be called when scheduling tasks to create/update launchd agent. Uses `uv run` to execute `scheduler-tick`.

4. **SSH Config Markers**: Never remove or modify marker comments manually. `SSHManager.remove_host_config()` relies on them to find blocks to remove.

5. **Time Parsing**: `parse_time_string()` handles multiple formats including relative ("tomorrow 09:30"), short form ("22:00" assumes today/tomorrow), and ISO format. Always returns timezone-aware datetime.

6. **Template Placeholders**: Only `{i}` placeholder is supported. Validation ensures it exists in `alias_template`.
