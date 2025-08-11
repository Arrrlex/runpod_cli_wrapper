import contextlib
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import runpod
import typer
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .config import (
    LOCAL_SETUP_FILE,
    REMOTE_SETUP_FILE,
    SSH_CONFIG_FILE,
    console,
    setup_runpod_api,
    validate_host_alias,
)
from .ssh_utils import remove_ssh_host_block, update_ssh_config
from .templates import load_templates


def run_local_command(command_list):
    typer.echo(f"-> Running: {' '.join(command_list)}")
    try:
        result = subprocess.run(
            command_list, check=True, capture_output=True, text=True
        )
        if result.stdout:
            typer.echo(result.stdout.strip())
        if result.stderr:
            typer.echo(result.stderr.strip(), err=True)
    except subprocess.CalledProcessError as e:
        typer.echo(f"❌ Command failed with exit code {e.returncode}:", err=True)
        if e.stdout:
            typer.echo("--- STDOUT ---", err=True)
            typer.echo(e.stdout.strip(), err=True)
        if e.stderr:
            typer.echo("--- STDERR ---", err=True)
            typer.echo(e.stderr.strip(), err=True)
        raise typer.Exit(1) from e


def run_local_command_stream(command_list):
    typer.echo(f"-> Running: {' '.join(command_list)}")
    try:
        with subprocess.Popen(
            command_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        ) as proc:
            assert proc.stdout is not None
            for line in proc.stdout:
                typer.echo(line.rstrip())
            returncode = proc.wait()
            if returncode != 0:
                typer.echo(f"❌ Command failed with exit code {returncode}", err=True)
                raise typer.Exit(1)
    except FileNotFoundError as e:
        typer.echo(f"❌ Command not found: {command_list[0]} ({e})", err=True)
        raise typer.Exit(1) from e


def _resume_pod_and_wait(pod_id: str, host_alias: str) -> dict:
    console.print(
        f"🚀 Resuming RunPod pod: [bold]{pod_id}[/bold] (alias: {host_alias})…"
    )
    try:
        runpod.resume_pod(pod_id, gpu_count=1)
    except Exception as e:
        pod = runpod.get_pod(pod_id)
        if pod.get("desiredStatus") != "RUNNING":
            reason = (str(e) or e.__class__.__name__).strip()
            typer.echo(f"❌ Failed to resume pod: {reason}", err=True)
            raise typer.Exit(1) from e
        else:
            console.print("[yellow]Pod was already running. Proceeding…[/yellow]")
    console.print("⏱️ Waiting for pod to be fully ready…")
    pod_details = None
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("Waiting for network…", total=None)
        for i in range(24):
            pod_details = runpod.get_pod(pod_id)
            if pod_details and pod_details.get("runtime") is not None:
                progress.update(
                    task, description="Pod is RUNNING and network is active"
                )
                break
            progress.update(task, description=f"Waiting… (attempt {i + 1}/24)")
            time.sleep(5)
        else:
            typer.echo("❌ Timed out waiting for pod to become ready.", err=True)
            raise typer.Exit(1) from None
    console.print(
        "✅ Pod is now [bold green]RUNNING[/bold green] and network is active."
    )
    return pod_details


def _extract_ssh_info(pod_details: dict) -> tuple[str, int]:
    ip_address = None
    port_number = None
    for port in pod_details.get("runtime", {}).get("ports", []):
        if port.get("privatePort") == 22 and port.get("isIpPublic") is True:
            ip_address = port["ip"]
            port_number = port["publicPort"]
            break
    if not ip_address or not port_number:
        typer.echo("❌ Could not find public SSH port information.", err=True)
        raise typer.Exit(1) from None
    console.print(f"Found IP: [bold]{ip_address}[/bold]")
    console.print(f"Found Port: [bold]{port_number}[/bold]")
    return ip_address, port_number


def _run_setup_scripts(host_alias: str) -> None:
    if LOCAL_SETUP_FILE.exists():
        console.print("⚙️  Running local setup…")
        local_setup_script = LOCAL_SETUP_FILE.read_text()
        run_local_command(["bash", "-lc", local_setup_script])
    if REMOTE_SETUP_FILE.exists():
        console.print("⚙️  Running remote setup via scp…")
        remote_setup_script = REMOTE_SETUP_FILE.read_text()
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".sh", prefix="setup_pod_"
        ) as temp_script:
            temp_script.write(remote_setup_script)
            local_script_path = Path(temp_script.name)
        remote_script_path = "/tmp/setup_pod.sh"
        console.print("    2. Copying setup script to pod…")
        run_local_command(
            [
                "scp",
                "-o",
                "StrictHostKeyChecking=no",
                str(local_script_path),
                f"{host_alias}:{remote_script_path}",
            ]
        )
        console.print("    3. Making script executable…")
        run_local_command(["ssh", host_alias, f"chmod +x {remote_script_path}"])
        console.print("    4. Executing setup script on pod (this may take a minute)…")
        run_local_command_stream(["ssh", host_alias, remote_script_path])
        local_script_path.unlink()
        console.print("✅ Remote setup complete.")


def stop_impl(host_alias: str) -> None:
    pod_configs = load_pod_configs()
    pod_id = pod_configs.get(host_alias)
    if not pod_id:
        typer.echo(f"❌ Unknown host alias: {host_alias}", err=True)
        raise typer.Exit(1)
    console.print(
        f"🛑 Stopping RunPod pod: [bold]{pod_id}[/bold] (alias: {host_alias})…"
    )
    try:
        runpod.stop_pod(pod_id)
        typer.echo(f"✅ Pod {pod_id} has been stopped.")
        removed = remove_ssh_host_block(host_alias)
        if removed:
            console.print(
                f"🧹 Removed SSH config block for '[bold]{host_alias}[/bold]'"
            )
    except Exception as e:
        try:
            pod = runpod.get_pod(pod_id)
            if pod.get("desiredStatus") == "EXITED":
                console.print(f"✅ Pod {pod_id} was already stopped.")
                removed = remove_ssh_host_block(host_alias)
                if removed:
                    console.print(
                        f"🧹 Removed SSH config block for '[bold]{host_alias}[/bold]'"
                    )
            else:
                typer.echo(f"❌ An unexpected error occurred: {e}", err=True)
                raise typer.Exit(1)
        except Exception as get_e:
            typer.echo(
                f"❌ An error occurred while stopping and checking the pod: {get_e}",
                err=True,
            )
            raise typer.Exit(1) from None


def start_pod(host_alias: str) -> None:
    setup_runpod_api()
    pod_id = validate_host_alias(host_alias)
    pod_details = _resume_pod_and_wait(pod_id, host_alias)
    ip_address, port_number = _extract_ssh_info(pod_details)
    console.print(f"📝 Updating local SSH config file: [dim]{SSH_CONFIG_FILE}[/dim]")
    update_ssh_config(host_alias, pod_id, ip_address, port_number)
    console.print("✅ Local SSH config updated successfully.")
    _run_setup_scripts(host_alias)


def deploy_from_template(
    template_alias: str,
    save_alias: str | None,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[str, str]:
    """Create a new pod from a stored template and return (alias, pod_id)."""
    setup_runpod_api()
    templates = load_templates()
    tmpl = templates.get(template_alias)
    if not tmpl:
        typer.echo(f"❌ Unknown template: {template_alias}", err=True)
        raise typer.Exit(1)

    alias = save_alias or f"rp-{template_alias}-{uuid.uuid4().hex[:6]}"
    container = tmpl.get("container") or {}
    image_name = container.get("imageName")
    if not image_name:
        typer.echo("❌ Template is missing container.imageName", err=True)
        raise typer.Exit(1)
    gpu_count = int(tmpl.get("gpuCount") or 1)
    container_disk = int(container.get("containerDiskInGb") or 20)
    volume_gb = int(container.get("volumeInGb") or 0)
    ports = container.get("ports") or [22]
    env = container.get("env") or {}
    start_script = container.get("startScript") or ""

    if dry_run:
        console.print("[bold]DRY RUN[/bold] Would create pod with:")
        console.print_json(
            data={
                "alias": alias,
                "imageName": image_name,
                "gpuCount": gpu_count,
                "containerDiskInGb": container_disk,
                "volumeInGb": volume_gb,
                "ports": ports,
                "env": env,
                "startScript": bool(start_script),
            }
        )
        return alias, "dry-run"

    try:
        create_fn = getattr(runpod, "create_pod", None) or getattr(
            runpod, "create_pod_v2", None
        )
        if not create_fn:
            typer.echo(
                "❌ This version of the runpod SDK does not provide pod creation. Phase 1 requires SDK create; REST fallback arrives in Phase 2.",
                err=True,
            )
            raise typer.Exit(1)
        pod = create_fn(
            image_name=image_name,
            gpu_count=gpu_count,
            container_disk_in_gb=container_disk,
            volume_in_gb=volume_gb,
            ports=ports,
            env=env,
            start_script=start_script,
        )
    except Exception as e:
        typer.echo(f"❌ Failed to create pod: {e}", err=True)
        raise typer.Exit(1) from e

    pod_id = None
    if isinstance(pod, dict):
        pod_id = pod.get("id") or pod.get("podId")
    if not pod_id:
        with contextlib.suppress(Exception):
            pod_id = pod.id  # type: ignore[attr-defined]
    if not pod_id:
        typer.echo("❌ Could not determine created pod id.", err=True)
        raise typer.Exit(1)

    console.print(f"🚀 Created pod [bold]{pod_id}[/bold]. Waiting for network…")
    pod_details = None
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("Waiting for RUNNING + network…", total=None)
        for i in range(36):
            pod_details = runpod.get_pod(pod_id)
            if pod_details and pod_details.get("runtime") is not None:
                desired = str(pod_details.get("desiredStatus") or "").upper()
                if desired == "RUNNING":
                    break
            progress.update(task, description=f"Waiting… (attempt {i + 1}/36)")
            time.sleep(5)
        else:
            typer.echo("❌ Timed out waiting for pod to become ready.", err=True)
            raise typer.Exit(1)
    ip_address, port_number = _extract_ssh_info(pod_details or {})
    console.print(f"📝 Updating local SSH config file: [dim]{SSH_CONFIG_FILE}[/dim]")
    update_ssh_config(alias, pod_id, ip_address, port_number)
    console.print("✅ Local SSH config updated successfully.")
    _run_setup_scripts(alias)
    return alias, pod_id


# Imports placed at the end to avoid circulars
from .config import load_pod_configs  # noqa: E402
