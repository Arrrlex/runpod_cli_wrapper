import contextlib
import json

import typer

from .config import TEMPLATES_FILE, ensure_config_dir_exists


def load_templates() -> dict:
    try:
        with TEMPLATES_FILE.open("r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items()}
            return {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        typer.echo(f"⚠️  Templates file is not valid JSON: {TEMPLATES_FILE}", err=True)
        return {}


def save_templates(templates: dict) -> None:
    ensure_config_dir_exists()
    with TEMPLATES_FILE.open("w") as f:
        json.dump(templates, f, indent=2, sort_keys=True)
        f.write("\n")


def _coalesce(*values, default=None):
    for v in values:
        if v not in (None, ""):
            return v
    return default


def derive_template_from_pod(
    pod: dict, template_name: str, include_env: bool = True
) -> dict:
    runtime = pod.get("runtime") or {}
    container = pod.get("container") or pod.get("template", {}).get("container") or {}
    image_name = _coalesce(
        container.get("imageName"),
        pod.get("imageName"),
        pod.get("template", {}).get("imageName"),
    )
    port_list: list[int] = []
    for p in runtime.get("ports") or []:
        with contextlib.suppress(Exception):
            private_port = int(p.get("privatePort"))
            if private_port not in port_list:
                port_list.append(private_port)
    env_vars = {}
    if include_env:
        env_vars = (
            (container.get("env") or {})
            if isinstance(container.get("env"), dict)
            else (pod.get("env") or {})
        )
    template: dict = {
        "name": template_name,
        "container": {
            "imageName": image_name or "",
            "containerDiskInGb": _coalesce(
                container.get("containerDiskInGb"), pod.get("containerDiskInGb"), 20
            ),
            "volumeInGb": _coalesce(
                container.get("volumeInGb"), pod.get("volumeInGb"), 0
            ),
            "ports": port_list or [22],
            "env": env_vars,
            "startScript": _coalesce(
                container.get("startScript"), pod.get("startScript"), ""
            ),
        },
        "gpuCount": int(_coalesce(pod.get("gpuCount"), 1)),
        "templateId": _coalesce(
            pod.get("templateId"), pod.get("template", {}).get("id")
        ),
    }
    return template
