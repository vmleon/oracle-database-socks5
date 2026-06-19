# Deploy — Oracle JDBC over SOCKS5 PoC

Ordered, copy-pasteable steps to go from zero to a running health endpoint.

---

## 1. Prerequisites

| Tool                      | Minimum version | Notes                                                                           |
| ------------------------- | --------------- | ------------------------------------------------------------------------------- |
| OCI tenancy + compartment | —               | Policies for `autonomous-database`, `instance-family`, `virtual-network-family` |
| OCI CLI                   | current         | `oci setup config` complete                                                     |
| Terraform                 | ≥ 1.6           | `oracle/oci` provider (pulled automatically)                                    |
| Python                    | 3.11+           | Create `.venv` and `pip install -e .` before any `manage.py` call (see step 3)  |
| JDK                       | 21 LTS          | `JAVA_HOME` pointing at JDK 21                                                  |
| OpenSSH                   | —               | `ssh`, `ssh-keygen` on PATH (key pair + optional jump host troubleshooting)     |
| curl                      | —               | Used by `manage.py health`                                                      |

> The Java build uses the committed Gradle wrapper (`./gradlew`) — no system Gradle or Maven installation is needed. Ansible is **not** required locally: the jump host installs it and runs the `socks5` role itself via cloud-init. `unzip` is used locally to expand the generated wallet.

---

## 2. What you need before you start

You do not collect OCIDs or CIDRs by hand — `manage.py setup` (step 3) discovers them. You only need:

- A working OCI CLI config at `~/.oci/config`. Run `oci setup config` if you have not already.
- An SSH key pair in `~/.ssh` (or any path you can point `setup` at).

From those, `setup` lists your subscribed regions and accessible compartments, auto-detects your public IP for the jump host allowlist, and generates the database password.

---

## 3. Configure

Create the virtual environment.

```bash
python3 -m venv .venv
```

Activate it. Do this once per shell session — every `manage.py` command below assumes the environment is active.

```bash
source .venv/bin/activate
```

Install the orchestrator and its dependencies into it. `pip install -e .` reads `pyproject.toml` and installs the Python packages `manage.py` needs (typer, python-dotenv, the OCI SDK, InquirerPy, rich). The `-e` ("editable") flag links the project in place rather than copying it, so edits to `manage.py` take effect immediately and the dependency list stays defined in one file.

```bash
pip install -e .
```

Run the interactive setup. It checks your tools, reads `~/.oci/config`, then prompts you to:

- pick the OCI profile,
- pick the region from your subscribed regions,
- pick the compartment from a searchable list,
- confirm the client CIDR (pre-filled with `<your-public-ip>/32`),
- pick the SSH key,
- choose the database name, the auth mode (`mtls` or `tls`), and whether to create the OCI Bastion demo path.

It generates the database password and writes `.env` and `infra/terraform/terraform.tfvars`. Nothing is edited by hand.

```bash
python manage.py setup
```

---

## 4. Provision everything

This single step does all of the OCI work. It runs `terraform init` automatically and reads `terraform.tfvars` written by `setup`:

- Creates the VCN, public subnet (jump host), private subnet (ADB-S private endpoint), NSGs, and the ADB instance (plus the OCI Bastion, only if you enabled it in `setup`).
- Packages the Ansible `socks5` role, uploads it to an Object Storage bucket, and issues a time-limited pre-authenticated request (PAR) URL.
- The jump host **self-provisions on first boot via cloud-init**: it installs Ansible, downloads the role through the PAR, and runs it locally — installing and hardening the Dante SOCKS5 daemon (`sockd`), restricting ingress to `CLIENT_CIDR` with firewalld, and allowing egress only to the ADB endpoint on 1522. No SSH push is involved.
- For `auth_mode = mtls`, it generates the ADB wallet and unzips it into `./wallet` on this machine (the client that runs the app). The jump host never receives the wallet — it stays a dumb relay. The wallet is freshly generated, so it always carries current (G2) DigiCert roots.

```bash
python manage.py tf apply
```

`terraform apply` returns once the compute instance is created, but the jump host's cloud-init keeps running for ~1–2 minutes after that (installing packages, running Ansible). The next step waits for it.

---

## 5. Verify connectivity

Print the deployment details plus ready-to-paste commands (SSH to the jump host with your key, the SOCKS relay test, the provisioning-log tail):

```bash
python manage.py info
```

Check that the jump host is reachable on port 1080. Until cloud-init finishes provisioning `sockd`, this shows `DOWN`; give it a minute or two after `tf apply`, then expect `<IP>:1080 reachable`.

```bash
python manage.py socks status
```

Independently verify the SOCKS relay carries TCP to the ADB port. A successful TCP handshake (then a closed connection) confirms the relay and remote DNS resolution both work.

```bash
curl -v --socks5-hostname <JUMPHOST_IP>:1080 telnet://<ADB_PRIVATE_FQDN>:1522
```

To watch cloud-init provisioning or troubleshoot the daemon, SSH in (port 22 is open to your `CLIENT_CIDR`):

```bash
ssh -i <your-ssh-key> opc@<JUMPHOST_IP> 'sudo tail -n 80 /var/log/socks5-bootstrap.log'
```

---

## 6. Build and run the application

Build the application JAR. This runs `./gradlew bootJar` inside `app/`; the output JAR lands in `app/build/libs/`.

```bash
python manage.py build
```

Launch the JAR with all environment variables from `.env` and Terraform output. The app binds on port 8080.

```bash
python manage.py run
```

Confirm connectivity. This calls `GET localhost:8080/actuator/health` and exits non-zero if the status is not `UP`. A successful response includes the database sub-check, which confirms an end-to-end query executed through the SOCKS5 proxy.

```bash
python manage.py health
```

---

## 7. Optional: Bastion demo path

If you chose to create the Bastion during `setup` (so `enable_bastion = true` in `terraform.tfvars`), you can route JDBC through OCI Bastion instead of the jump host. The Bastion path is ephemeral (3-hour hard session TTL) and is intended for demo/ad-hoc access only.

Create a Bastion dynamic port-forwarding session via the OCI Console or CLI, then open the local SOCKS tunnel manually:

```bash
ssh -N -D 127.0.0.1:1080 -p 22 -i ~/.ssh/id_rsa \
    -o StrictHostKeyChecking=no \
    <session-ocid>@host.bastion.<region>.oci.oraclecloud.com
```

Update `.env`:

```bash
MODE=bastion
SOCKS_HOST=127.0.0.1
SOCKS_PORT=1080
```

Then run `python manage.py run` and `python manage.py health` as usual. The JDBC behavior is identical; only `SOCKS_HOST` changes.

---

## 8. Teardown

Remove `wallet/` contents and run `terraform destroy` to tear down all OCI resources.

```bash
python manage.py clean --destroy
```

---

## Troubleshooting

### NSG rules: ports 1522 and 1080

The jump host NSG must allow:

- **Ingress** TCP 22 and 1080 from `CLIENT_CIDR` (operator/client IP).
- **Egress** TCP 1522 to the ADB NSG (private endpoint).

The ADB NSG must allow ingress TCP 1522 from the jump host NSG or the jump host private IP. Verify in the OCI Console under **Networking → Virtual Cloud Networks → your VCN → Network Security Groups** if connectivity is failing.

### Remote DNS failure (`host not found` on connection)

`oracle.net.socksRemoteDNS` must be `true`. The ADB private FQDN only resolves inside the VCN; with remote DNS disabled the JDBC driver tries to resolve it locally (fails) and sends a raw IP in the SOCKS CONNECT request (wrong address). The app configures this correctly; if you see `host not found` or `IO error` at connection time, verify the property is set in `application.yml` or that no system property is overriding it.

### Wallet file permissions

All files under `wallet/` must be `chmod 600` and owned by the user running the JVM. `tf apply` sets this automatically when it unzips the generated wallet. If you unzip manually, run:

```bash
chmod 600 wallet/*
```

### DigiCert G1 vs G2 wallet roots

Wallets minted before 28 Jan 2026 carry DigiCert G1 roots. G1 roots are not trusted after 15 Apr 2026. If you see `PKIX path building failed`, `certificate unknown`, or similar TLS errors, your wallet is stale. Fix: `python manage.py clean` then `python manage.py tf apply` to regenerate a current (G2) wallet into `./wallet`.

### Jump host shape unavailable (`Out of host capacity` / image not found)

The jump host defaults to `VM.Standard.E5.Flex`. If `tf apply` reports `Out of host capacity` or the image lookup is empty, that shape is not available in your region or trial tenancy. Set a different shape in `infra/terraform/terraform.tfvars`, for example `jumphost_shape = "VM.Standard.E4.Flex"` or `jumphost_shape = "VM.Standard.A1.Flex"` (Always Free, Ampere), then re-run `python manage.py tf apply`. Availability domains are listed against the tenancy, and the Oracle Linux 9 image is selected by name (not by shape), so neither depends on the chosen shape.

### Dante directive names across versions

Older Dante builds use `method:` while newer ones use `socksmethod:` (within a `socks pass {}` block). On Oracle Linux the daemon is `sockd`, its config is `/etc/sockd.conf`, and its logs go to `journalctl -u sockd` (or `/var/log/sockd.log` when `socks_debug` is on). Check the installed version with `rpm -q dante-server` and match the directives to that version's `man sockd.conf`. The Ansible role renders config for the EPEL build.

### `tnsping` does not traverse SOCKS

`tnsping` uses the Oracle thick client (C libraries), which does not honour `oracle.net.socks*` JDBC properties. A failing `tnsping` alongside a working JDBC connection is expected and normal — use `python manage.py health` or the `curl --socks5-hostname` test in step 7 to verify connectivity instead.
