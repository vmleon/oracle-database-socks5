#!/usr/bin/env python3
"""Orchestrator CLI for the Oracle SOCKS5 JDBC PoC."""
import configparser
import json
import os
import secrets
import socket
import subprocess
import urllib.request
from pathlib import Path

import typer
from dotenv import dotenv_values

app = typer.Typer(no_args_is_help=True)
TF_DIR = Path("infra/terraform")
ENV_FILE = Path(".env")
TFVARS_FILE = TF_DIR / "terraform.tfvars"

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


_SECRET_FLAGS = {"--password", "--admin-password"}


def _sh(cmd, **kw):
    safe = ["***" if i > 0 and cmd[i - 1] in _SECRET_FLAGS else a for i, a in enumerate(cmd)]
    typer.echo("$ " + " ".join(safe))
    return subprocess.run(cmd, check=True, **kw)


# --- setup helpers ---------------------------------------------------------

def _read_oci_config():
    path = Path.home() / ".oci" / "config"
    if not path.exists():
        typer.echo(f"OCI config not found at {path}. Run 'oci setup config' first.")
        raise typer.Exit(1)
    parser = configparser.ConfigParser()
    parser.read(path)
    profiles = list(parser.sections())
    if parser.defaults():
        profiles.insert(0, "DEFAULT")
    return profiles, parser


def _sdk_config(parser, profile):
    p = parser[profile]
    return {
        "user": p.get("user"),
        "key_file": p.get("key_file"),
        "fingerprint": p.get("fingerprint"),
        "tenancy": p.get("tenancy"),
        "region": p.get("region", "us-phoenix-1"),
    }


def _list_regions(sdk_config):
    import oci

    try:
        client = oci.identity.IdentityClient(sdk_config)
        tenancy_id = sdk_config["tenancy"]
        home = client.get_tenancy(tenancy_id).data.home_region_key
        subs = client.list_region_subscriptions(tenancy_id).data
        regions = [{"name": s.region_name, "is_home": s.region_key == home} for s in subs]
        regions.sort(key=lambda x: (not x["is_home"], x["name"]))
        return regions
    except Exception as e:
        typer.echo(f"Warning: could not fetch regions: {e}")
        return None


def _list_compartments(sdk_config):
    import oci

    try:
        client = oci.identity.IdentityClient(sdk_config)
        tenancy_id = sdk_config["tenancy"]
        tenancy = client.get_compartment(tenancy_id).data
        comps = [{"name": f"{tenancy.name} (root)", "id": tenancy_id}]
        resp = oci.pagination.list_call_get_all_results(
            client.list_compartments,
            compartment_id=tenancy_id,
            compartment_id_in_subtree=True,
            access_level="ACCESSIBLE",
        )
        for c in resp.data:
            if c.lifecycle_state == "ACTIVE":
                comps.append({"name": c.name, "id": c.id})
        return comps
    except Exception as e:
        typer.echo(f"Warning: could not fetch compartments: {e}")
        return None


def _generate_password(length=20):
    """Oracle-compliant: starts with a letter, 2+ specials, 2+ digits."""
    letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    digits = "0123456789"
    specials = "#_-"
    pw = [secrets.choice(letters), secrets.choice(specials), secrets.choice(specials),
          secrets.choice(digits), secrets.choice(digits)]
    alphabet = letters + digits + specials
    pw += [secrets.choice(alphabet) for _ in range(length - 5)]
    tail = pw[1:]
    secrets.SystemRandom().shuffle(tail)
    pw[1:] = tail
    return "".join(pw)


def _detect_public_ip():
    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
        try:
            return urllib.request.urlopen(url, timeout=5).read().decode().strip()
        except Exception:
            continue
    return None


def _write_env(values):
    ENV_FILE.write_text("".join(f'{k}="{v}"\n' for k, v in values.items()))


def _write_tfvars(values):
    lines = []
    for k, v in values.items():
        if isinstance(v, bool):
            lines.append(f"{k} = {str(v).lower()}")
        else:
            lines.append(f'{k} = "{v}"')
    TFVARS_FILE.write_text("\n".join(lines) + "\n")


# --- commands --------------------------------------------------------------

@app.command()
def setup():
    """Interactive OCI configuration. Writes .env and terraform.tfvars — no manual editing."""
    from InquirerPy import inquirer
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    console.print("[bold]Oracle JDBC over SOCKS5 — Setup[/bold]\n")

    console.print("Checking tools:")
    for tool in ["oci", "terraform", "ansible", "java", "gradle", "ssh"]:
        ok = subprocess.run(["which", tool], capture_output=True).returncode == 0
        console.print(f"  {'[green]OK[/green]     ' if ok else '[red]MISSING[/red]'} {tool}")

    profiles, parser = _read_oci_config()
    profile = inquirer.select(
        message="OCI profile:", choices=profiles, default=profiles[0]
    ).execute()
    sdk_config = _sdk_config(parser, profile)

    console.print("\nFetching subscribed regions...")
    regions = _list_regions(sdk_config)
    if regions:
        choices = [f"{r['name']} (home)" if r["is_home"] else r["name"] for r in regions]
        region = inquirer.select(
            message="Region:", choices=choices, default=choices[0]
        ).execute().replace(" (home)", "")
    else:
        region = inquirer.text(message="Region:", default=sdk_config["region"]).execute()
    sdk_config["region"] = region

    console.print("\nFetching compartments...")
    comps = _list_compartments(sdk_config)
    if comps:
        comp_map = {c["name"]: c["id"] for c in comps}
        selected = inquirer.fuzzy(
            message="Compartment (type to search):", choices=list(comp_map)
        ).execute()
        compartment_ocid = comp_map[selected]
    else:
        compartment_ocid = inquirer.text(message="Compartment OCID:").execute()

    detected = _detect_public_ip()
    if detected:
        console.print(f"\nDetected your public IP: [bold]{detected}[/bold]")
    client_cidr = inquirer.text(
        message="Client CIDR (your egress IP — locks jump host ingress):",
        default=f"{detected}/32" if detected else "0.0.0.0/0",
    ).execute()

    ssh_dir = Path.home() / ".ssh"
    keys = (
        sorted(
            f.name for f in ssh_dir.iterdir()
            if f.is_file() and not f.suffix and f.with_suffix(".pub").exists()
        )
        if ssh_dir.is_dir() else []
    )
    if keys:
        ssh_private = str(ssh_dir / inquirer.fuzzy(message="SSH private key:", choices=keys).execute())
    else:
        ssh_private = inquirer.text(message="SSH private key path:").execute()
    ssh_public_path = ssh_private + ".pub"
    if Path(ssh_public_path).exists():
        ssh_public_key = Path(ssh_public_path).read_text().strip()
    else:
        ssh_public_key = inquirer.text(message="SSH public key (paste content):").execute()

    db_name = inquirer.text(
        message="Database name (used for resource naming + TNS alias):", default="dbpoc"
    ).execute()
    auth_mode = inquirer.select(
        message="Auth mode:", choices=["mtls", "tls"], default="mtls"
    ).execute()
    enable_bastion = inquirer.confirm(
        message="Also create the OCI Bastion demo path?", default=False
    ).execute()

    db_password = _generate_password()
    tns_alias = f"{db_name}_high"
    wallet_path = "./wallet" if auth_mode == "mtls" else ""

    console.print(Panel(
        f"Profile:       {profile}\n"
        f"Region:        {region}\n"
        f"Compartment:   {compartment_ocid}\n"
        f"Client CIDR:   {client_cidr}\n"
        f"SSH key:       {ssh_private}\n"
        f"Database name: {db_name}\n"
        f"Auth mode:     {auth_mode}\n"
        f"Bastion:       {enable_bastion}\n"
        f"DB password:   (generated — stored in .env and terraform.tfvars)",
        title="Configuration Summary",
    ))
    if not inquirer.confirm(message="Save configuration?", default=True).execute():
        console.print("[yellow]Setup cancelled.[/yellow]")
        raise typer.Exit(0)

    _write_env({
        "OCI_PROFILE": profile,
        "OCI_REGION": region,
        "COMPARTMENT_OCID": compartment_ocid,
        "CLIENT_CIDR": client_cidr,
        "SSH_PRIVATE_KEY_PATH": ssh_private,
        "SSH_PUBLIC_KEY_PATH": ssh_public_path,
        "ENABLE_BASTION": "true" if enable_bastion else "false",
        "MODE": "jumphost",
        "SOCKS_PORT": "1080",
        "AUTH_MODE": auth_mode,
        "TNS_ALIAS": tns_alias,
        "WALLET_PATH": wallet_path,
        "DB_USER": "ADMIN",
        "DB_PASSWORD": db_password,
    })
    _write_tfvars({
        "oci_profile": profile,
        "region": region,
        "tenancy_ocid": sdk_config["tenancy"],
        "compartment_ocid": compartment_ocid,
        "client_cidr": client_cidr,
        "ssh_public_key": ssh_public_key,
        "db_admin_password": db_password,
        "db_name": db_name,
        "auth_mode": auth_mode,
        "enable_bastion": enable_bastion,
    })

    console.print(f"\n[green]Wrote {ENV_FILE} and {TFVARS_FILE}[/green]")
    console.print("\nNext step: [bold]python manage.py tf apply[/bold]")


@app.command()
def tf(action: str):
    """plan | apply | destroy (reads infra/terraform/terraform.tfvars)."""
    if action in ("plan", "apply"):
        _sh(["terraform", "init", "-input=false"], cwd=TF_DIR)
    args = ["terraform", action]
    if action in ("apply", "destroy"):
        args.append("-auto-approve")
    _sh(args, cwd=TF_DIR)


@app.command()
def provision():
    """Render inventory from TF output and run the ansible socks5 role."""
    cfg = load_config({})
    tf = _read_tf_output()
    ip = _tf_value(tf, "jumphost_public_ip")
    fqdn = _tf_value(tf, "adb_private_endpoint")
    key = cfg.get("SSH_PRIVATE_KEY_PATH", "~/.ssh/id_rsa")
    inv = Path("ansible/inventory.ini")
    inv.write_text(
        f"[jumphost]\n{ip} ansible_user=opc ansible_ssh_private_key_file={key}\n"
    )
    _sh(["ansible-playbook", "-i", "inventory.ini", "socks5.yml",
         "-e", f"adb_fqdn={fqdn}", "-e", f"client_cidr={cfg.get('CLIENT_CIDR', '0.0.0.0/0')}"],
        cwd="ansible")


@app.command()
def wallet(action: str = "fetch"):
    """Download + unzip the ADB wallet (fresh G2) into wallet/."""
    cfg = load_config({})
    tf = _read_tf_output()
    adb_id = _tf_value(tf, "adb_id")
    Path("wallet").mkdir(exist_ok=True)
    pwd = cfg.get("DB_PASSWORD")
    if not pwd:
        typer.echo("DB_PASSWORD not set — run 'manage.py setup'")
        raise typer.Exit(1)
    _sh(["oci", "db", "autonomous-database", "generate-wallet",
         "--profile", cfg.get("OCI_PROFILE", "DEFAULT"),
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
