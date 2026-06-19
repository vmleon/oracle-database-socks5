#!/usr/bin/env python3
"""Orchestrator CLI for the Oracle SOCKS5 JDBC PoC."""
import json
import os
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


def load_config(cli_overrides: dict) -> dict:
    cfg = {}
    tf = _read_tf_output()
    for tf_key, cfg_key in _TF_MAP.items():
        if tf_key in tf:
            cfg[cfg_key] = tf[tf_key]["value"]
    cfg.update(_read_env())
    cfg.update({k: v for k, v in cli_overrides.items() if v is not None})
    return cfg
