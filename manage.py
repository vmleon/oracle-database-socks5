#!/usr/bin/env python3
"""Orchestrator CLI for the Oracle SOCKS5 JDBC PoC."""
import json
import os
import socket
import subprocess
from pathlib import Path

import typer
from dotenv import dotenv_values

app = typer.Typer(no_args_is_help=True)
TF_DIR = Path("infra/terraform")

# maps terraform output keys -> config keys
_TF_MAP = {"jumphost_public_ip": "SOCKS_HOST", "adb_private_endpoint": "ADB_FQDN"}


def _read_env() -> dict:
    return {k: v for k, v in dotenv_values(".env").items() if v is not None}


def _read_tf_output() -> dict:
    try:
        out = subprocess.run(
            ["terraform", "output", "-json"], cwd=TF_DIR,
            capture_output=True, text=True, check=True,
        ).stdout
        return json.loads(out or "{}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}


def _tf_value(tf, key):
    if key not in tf:
        typer.echo(f"missing terraform output '{key}' — run 'manage.py tf apply' first")
        raise typer.Exit(1)
    return tf[key]["value"]


def load_config(cli_overrides: dict) -> dict:
    cfg = {}
    tf = _read_tf_output()
    for tf_key, cfg_key in _TF_MAP.items():
        if tf_key in tf:
            cfg[cfg_key] = tf[tf_key]["value"]
    cfg.update(_read_env())
    cfg.update({k: v for k, v in cli_overrides.items() if v is not None})
    return cfg


def _sh(cmd, **kw):
    typer.echo("$ " + " ".join(cmd))
    return subprocess.run(cmd, check=True, **kw)


@app.command()
def setup():
    """Check prereqs and seed .env."""
    for tool in ["oci", "terraform", "ansible", "java", "gradle", "ssh"]:
        ok = subprocess.run(["which", tool], capture_output=True).returncode == 0
        typer.echo(f"  {'OK ' if ok else 'MISSING'} {tool}")
    if not Path(".env").exists():
        Path(".env").write_text(Path(".env.example").read_text())
        typer.echo("seeded .env from .env.example")


@app.command()
def tf(action: str, enable_bastion: bool = False):
    """plan | apply | destroy"""
    args = ["terraform", action]
    if action in ("apply", "destroy"):
        args.append("-auto-approve")
    if action in ("plan", "apply"):
        args += ["-var", f"enable_bastion={'true' if enable_bastion else 'false'}"]
    _sh(args, cwd=TF_DIR)


@app.command()
def provision():
    """Render inventory from TF output and run the ansible socks5 role."""
    cfg = load_config({})
    tf = _read_tf_output()
    ip = _tf_value(tf, "jumphost_public_ip")
    fqdn = _tf_value(tf, "adb_private_endpoint")
    inv = Path("ansible/inventory.ini")
    inv.write_text(
        f"[jumphost]\n{ip} ansible_user=ubuntu "
        f"ansible_ssh_private_key_file=~/.ssh/id_rsa\n"
    )
    _sh(["ansible-playbook", "-i", "inventory.ini", "socks5.yml",
         "-e", f"adb_fqdn={fqdn}", "-e", f"client_cidr={cfg.get('CLIENT_CIDR','0.0.0.0/0')}"],
        cwd="ansible")


@app.command()
def wallet(action: str = "fetch"):
    """Download + unzip the ADB wallet (fresh G2) into wallet/."""
    cfg = load_config({})
    tf = _read_tf_output()
    adb_id = _tf_value(tf, "adb_id")
    Path("wallet").mkdir(exist_ok=True)
    pwd = cfg.get("DB_PASSWORD", "Welcome_12345#")
    _sh(["oci", "db", "autonomous-database", "generate-wallet",
         "--autonomous-database-id", adb_id,
         "--password", pwd, "--file", "wallet/wallet.zip"])
    _sh(["unzip", "-o", "wallet/wallet.zip", "-d", "wallet"])
    for p in Path("wallet").glob("*"):
        p.chmod(0o600)


@app.command()
def socks(action: str, mode: str = "jumphost", port: int = 1080):
    """status | up | down  (up/down are bastion-only)."""
    cfg = load_config({"MODE": mode})
    if action == "status":
        host = cfg.get("SOCKS_HOST", "127.0.0.1")
        try:
            with socket.create_connection((host, port), timeout=3):
                pass
            typer.echo(f"{host}:{port} reachable")
            raise typer.Exit(0)
        except OSError:
            typer.echo(f"{host}:{port} DOWN")
            raise typer.Exit(1)
    typer.echo("up/down implemented for bastion mode (see DEMO.md)")


@app.command()
def build():
    _sh(["./gradlew", "bootJar", "-q", "-x", "test"], cwd="app")


@app.command()
def run():
    cfg = load_config({})
    env = {**os.environ, **cfg}
    jar = next(Path("app/build/libs").glob("socks5poc-*.jar"))
    _sh(["java", "-jar", str(jar)], env=env)


@app.command()
def health():
    rc = subprocess.run(
        ["curl", "-sf", "localhost:8080/actuator/health"],
        capture_output=True, text=True,
    )
    typer.echo(rc.stdout)
    raise typer.Exit(0 if '"status":"UP"' in rc.stdout else 1)


@app.command()
def clean(destroy: bool = False):
    for p in Path("wallet").glob("*"):
        p.unlink()
    if destroy:
        _sh(["terraform", "destroy", "-auto-approve"], cwd=TF_DIR)


if __name__ == "__main__":
    app()
