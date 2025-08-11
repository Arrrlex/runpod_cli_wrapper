## RunPod CLI Wrapper – Deployment aliases, capacity fallback, and jank mitigation

### Goals

- Make deploying new pods trivial via local "pod config aliases" so you don’t need the web UI.
- Reduce impact of GPU capacity shortages by automatically trying fallback options.
- Detect and mitigate janky instances by running health checks and auto-retrying a fresh deployment.
- Integrate seamlessly with existing `rp` UX: config directory, alias management, SSH config, setup scripts, and scheduler.

### High-level UX

- `rp template derive <template_alias> --from <pod_id|host_alias>`: Create a reusable local template from an existing pod’s config.
- `rp template list|show|delete|export|import`: Manage templates.
- `rp deploy <template_alias> [--alias <ssh_alias>] [options...]`: Deploy a new pod using a template. On success, it auto-adds to `pods.json`, updates SSH config, and optionally runs setup scripts.
- `rp start <host_alias> [--create-new-on-capacity <template_alias>]`: If resuming the existing pod fails due to capacity, optionally create a new pod using the provided template.
- `rp stop ...` remains as-is; add `rp schedule deploy` in a follow-up phase if needed.

### Why this helps your pain points

- Capacity: If a stopped pod can’t resume, `rp deploy` can quickly create a replacement with predefined preferences (GPU/region/cloud fallbacks) and add it to your local aliases.
- Jank: Automated post-boot health checks (SSH reachability, optional remote smoke tests, optional network probe) can terminate bad pods and retry fresh ones, up to limits.

---

## Detailed Design

### Configuration files

- `~/.config/rp/templates.json` (new): Local deployment templates.
- `~/.config/rp/pods.json` (existing): Alias → pod_id map, unchanged.
- `~/.config/rp/schedule.json` (existing): For scheduler tasks, unchanged.

Schema for `templates.json` (example):

```json
{
  "sd-a100": {
    "name": "sd-a100",
    "cloudPreference": ["SECURE", "COMMUNITY"],
    "regionPreference": ["NA", "EU"],
    "gpuPreference": [
      "NVIDIA H100 80GB",
      "NVIDIA A100 80GB",
      "NVIDIA A5000 24GB"
    ],
    "gpuCount": 1,
    "container": {
      "imageName": "runpod/pytorch:latest",
      "containerDiskInGb": 20,
      "volumeInGb": 50,
      "ports": [22, 7860],
      "env": {
        "HF_HOME": "/root/.cache/huggingface"
      },
      "startScript": "#!/bin/bash\nset -euo pipefail\necho booting..."
    },
    "network": {
      "allowPublicIp": true
    },
    "health": {
      "sshTimeoutSeconds": 180,
      "smokeCommands": ["echo OK"],
      "speedTest": {
        "enabled": false,
        "url": "https://speed.hetzner.de/100MB.bin",
        "minMbps": 100
      },
      "maxRetries": 2,
      "retryBackoffSeconds": 10
    },
    "postDeploy": {
      "runLocalSetup": true,
      "runRemoteSetup": true
    },
    "tags": { "rp": "true", "purpose": "sd" }
  }
}
```

Notes:
- Keys map closely to common RunPod create APIs (container image, disk, volume, env, ports, gpu, cloud/region prefs).
- Health config is optional and defaults to basic SSH reachability.
- We assume account-level SSH key provisioning; expose `--ssh-public-key` override later if needed.

### CLI Additions (Typer commands)

1) Template management
- `rp template derive <template_alias> --from <pod_id|host_alias> [--include-env true|false]`:
  - Fetch `get_pod(<id>)`, map to template schema.
  - Optionally scrub or keep env. Save to `templates.json`.
- `rp template list`
- `rp template show <template_alias>`
- `rp template delete <template_alias>`
- `rp template export <template_alias> [-o file]` / `rp template import <file>`

2) Deployment
- `rp deploy <template_alias>` options:
  - `--alias <ssh_alias>`: SSH host alias. Default: `rp-<template>-<shortid>`
  - `--gpu <name>[,name...]`, `--region <code>[,code...]`, `--cloud <SECURE|COMMUNITY|ALL>`: Override preferences.
  - `--count <n>`: GPU count (default from template).
  - `--env KEY=VAL` (repeatable): Inline env overrides.
  - `--no-local-setup`, `--no-remote-setup`: Skip setup scripts.
  - `--retries <n>` and `--retry-backoff-seconds <n>`: Override health retry policy.
  - `--speed-test-url <url>`, `--min-mbps <N>`: Enable simple network check.
  - `--schedule-stop-in "2h"` (optional): Chain a stop schedule.
  - `--dry-run`.

3) Start with capacity fallback (optional but useful)
- `rp start <host_alias> --create-new-on-capacity <template_alias>`:
  - Attempt `resume_pod` as today.
  - If capacity error, automatically call `deploy <template_alias>` and continue as if started, adopting the new alias or reusing the same alias name (configurable: default reuse alias by overwriting `pods.json` entry).

### Deployment algorithm

Given a template and user overrides:
1. Build candidate matrix ordered by preference: `cloud × region × gpu`.
   - Defaults from template lists; overridden lists take precedence.
2. For each candidate:
   - Try `runpod.create_pod` with merged spec. If the Python SDK lacks create methods, fall back to REST.
   - Wait until `desiredStatus == RUNNING` and `runtime.ports` contains public SSH port (as in current `_resume_pod_and_wait`).
   - Update SSH config and run health checks:
     - SSH reachability with timeout.
     - Optional remote smoke commands via `ssh alias <cmd>`.
     - Optional speed test: `curl -L --max-time <t> -o /dev/null -s -w '%{speed_download}\n' <url>` and compare.
   - If health fails:
     - Terminate the pod (to avoid costs).
     - Backoff and try next candidate. Repeat up to `health.maxRetries` total attempts across candidates.
3. On success:
   - Persist `pods.json[alias] = pod_id`.
   - Optionally run local and remote setup scripts (reuse existing `_run_setup_scripts`).
   - Optionally schedule stop.

Safety:
- Always terminate failed/janky pods before moving on.
- If all candidates fail, exit non-zero with a clear report of attempts and errors.

### Mapping RunPod API to our schema

We will attempt to use the `runpod` Python SDK first:
- Already used: `get_pod`, `resume_pod`, `stop_pod`.
- Likely available: `create_pod`, `terminate_pod`, and listing helpers. If not, implement minimal REST calls using `requests` with the stored API key.

Create payload (illustrative; exact keys will match SDK/REST):
- `imageName`, `containerDiskInGb`, `volumeInGb`, `ports`, `env`, `startScript`.
- `gpuTypeId` (resolve from `gpuPreference` by name → id mapping) and `gpuCount`.
- `cloudType` / `region` (if required by API; otherwise the platform may auto-place).

Helpers to implement:
- `resolve_gpu_name_to_id(name) -> str`: cache a mapping by calling SDK/REST once.
- `create_pod_from_template(template, candidate) -> pod_id`.
- `wait_until_running_and_network(pod_id) -> pod_details` (reuse `_resume_pod_and_wait` logic pattern).
- `terminate_pod_safe(pod_id)`.

### Health check design

Default (always on):
- SSH reachability and ability to execute `echo OK` remotely.

Optional checks (toggle via template or CLI):
- Smoke commands (array): run each with `ssh alias <cmd>`. All must return 0.
- Network throughput: measure `speed_download` via `curl` on the pod and compare to a threshold. Disabled by default.

Retriable error classes:
- SSH timeout, command non-zero, HTTP/network errors in speed test.
- Capacity errors on `create_pod` (try next candidate). Unknown errors abort immediately.

### Data structures and functions (in code)

- Files/constants:
  - `TEMPLATES_FILE = CONFIG_DIR / "templates.json"`

- Utility fns:
  - `load_templates() -> dict`
  - `save_templates(dict) -> None`
  - `derive_template_from_pod(pod_details, include_env: bool) -> dict`
  - `merge_template_with_overrides(template, cli_overrides) -> resolved_spec`
  - `iterate_candidates(resolved_spec) -> generator[candidate]`
  - `create_and_validate(candidate) -> (pod_id, details)`

### Integration with existing features

- Reuse `update_ssh_config`, `_extract_ssh_info`, `_run_setup_scripts`.
- Reuse scheduler infra for optional `--schedule-stop-in` (parse as today and call `schedule_task_stop`).
- Keep `pods.json` exactly as-is for alias → id simplicity.

### Edge cases

- API key missing: covered by existing `setup_runpod_api()`.
- SSH key mismatches: document that RunPod uses account SSH keys; optional `--ssh-public-key` may be added later to inject per-pod.
- Template drift: warn if required fields are missing at deploy time; show `rp template show` guidance.
- Cleanup on SIGINT during deploy: best-effort terminate the in-flight pod if created.
- Overwriting existing alias: require `--force` or auto-generate alias suffix.

### Testing plan

- Unit-ish tests for pure helpers (template load/merge, candidate iteration, health decision logic).
- Manual E2E:
  - Derive template from a known-good pod, then deploy new pods.
  - Simulate jank by failing smoke command and observe auto-terminate + retry.
  - Simulate capacity failure by requesting scarce GPU and observe fallback.

### Incremental implementation plan (phased)

Phase 1 (core deploy and templates)
1. Add templates storage and `rp template derive|list|show|delete`.
2. Implement `rp deploy` minimal: choose first candidate only, create pod, wait, SSH update, add alias, run setup.
3. Docs and examples.

Phase 2 (fallbacks and health)
4. Add candidate matrix iteration (cloud/region/gpu), termination on failure, and retry policy.
5. Add smoke command support and `--speed-test-url` check (disabled by default).
6. Add `--schedule-stop-in` integration.

Phase 3 (polish)
7. Add import/export for templates.
8. Extend `start` with `--create-new-on-capacity` path.
9. Optional: `rp schedule deploy <template_alias> --at/--in` using existing scheduler.

### Time/effort estimate

- Phase 1: 0.5–1 day.
- Phase 2: 0.5–1.5 days (depends on SDK vs REST and health-check tuning).
- Phase 3: 0.5 day.

### Open questions / assumptions

- SDK coverage: If `runpod.create_pod`/`terminate_pod` or GPU/region listing is missing, we’ll add a thin REST client using the stored API key.
- GPU/region targeting: Some placement might be abstracted by RunPod; we’ll support whatever knobs the API exposes.
- Network speed test: Provide as opt-in; defaults to basic SSH/smoke checks for speed and cost.

---

## Acceptance criteria

- I can run `rp template derive base --from <existing_pod_or_alias>`.
- I can run `rp deploy base --alias my-new-pod` and immediately SSH using `ssh my-new-pod`.
- If deployment yields a bad pod (simulated via smoke command failure), it auto-terminates and retries up to configured limits.
- If capacity is unavailable for the preferred GPU, the deploy tries fallbacks and either succeeds or exits with a clear report.
- The new pod is added to `rp list`, and setup scripts run as they do in `rp start`.
