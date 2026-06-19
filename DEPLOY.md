# Deploy — Oracle JDBC over SOCKS5 PoC

Ordered, copy-pasteable steps to go from zero to a running health endpoint.

---

## 1. Prerequisites

| Tool                      | Minimum version | Notes                                                                           |
| ------------------------- | --------------- | ------------------------------------------------------------------------------- |
| OCI tenancy + compartment | —               | Policies for `autonomous-database`, `instance-family`, `virtual-network-family` |
| OCI CLI                   | current         | `oci setup config` complete                                                     |
| Terraform                 | ≥ 1.6           | Latest `hashicorp/oci` provider                                                 |
| Ansible                   | current         | `ansible-playbook` on PATH                                                      |
| Python                    | 3.11+           | `.venv` is created by `setup`; no system install needed                         |
| JDK                       | 21 LTS          | `JAVA_HOME` pointing at JDK 21                                                  |
| OpenSSH                   | —               | `ssh`, `ssh-keygen` on PATH                                                     |
| curl                      | —               | Used by `manage.py health`                                                      |

> The Java build uses the committed Gradle wrapper (`./gradlew`) — no system Gradle or Maven installation is needed.

---

## 2. Open parameters to pin before you start

Collect these four values; you will write them into `terraform.tfvars` and `.env` in step 3.

| Parameter          | Description                                                                                 | Example                        |
| ------------------ | ------------------------------------------------------------------------------------------- | ------------------------------ |
| `region`           | OCI region identifier                                                                       | `eu-frankfurt-1`               |
| `compartment_ocid` | Target compartment OCID                                                                     | `ocid1.compartment.oc1..xxxxx` |
| `client_cidr`      | Your public egress IP as a `/32` — used for jump host NSG ingress rules (ports 22 and 1080) | `203.0.113.10/32`              |
| `ssh_public_key`   | Contents of your SSH public key                                                             | `ssh-rsa AAAA…`                |

---

## 3. Configure

### 3a. Terraform variables

```bash
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
```

Edit `infra/terraform/terraform.tfvars` and set at minimum:

```hcl
region            = "eu-frankfurt-1"
compartment_ocid  = "ocid1.compartment.oc1..xxxxx"
client_cidr       = "203.0.113.10/32"
ssh_public_key    = "ssh-rsa AAAA…"
db_admin_password = "YourStr0ngPass#"
```

`enable_bastion = false` is the default (jump host only). Set `true` to also create the OCI Bastion resource for the demo path.

### 3b. Application environment

```bash
cp .env.example .env
```

Edit `.env`:

```bash
OCI_REGION=eu-frankfurt-1
COMPARTMENT_OCID=ocid1.compartment.oc1..xxxxx
CLIENT_CIDR=203.0.113.10/32
SSH_PUBLIC_KEY_PATH=~/.ssh/id_rsa.pub

MODE=jumphost
SOCKS_PORT=1080

AUTH_MODE=mtls
TNS_ALIAS=dbpoc_high
WALLET_PATH=./wallet
DB_USER=ADMIN
DB_PASSWORD=YourStr0ngPass#
```

(`SOCKS_HOST` is populated automatically from Terraform output; leave it blank for now.)

### 3c. Check prerequisites and seed the venv

```bash
python manage.py setup
```

This checks each required tool is on `PATH` and creates `.venv` with the Python dependencies.

---

## 4. Provision infrastructure

```bash
python manage.py tf apply
```

To also create the demo Bastion resource:

```bash
python manage.py tf apply --enable-bastion
```

Terraform provisions the VCN, public subnet (jump host), private subnet (ADB-S private endpoint), NSGs, and the ADB instance. The jump host public IP and ADB private FQDN are written to Terraform state and read automatically by subsequent `manage.py` commands.

---

## 5. Configure the jump host (SOCKS5 daemon)

```bash
python manage.py provision
```

This reads the jump host public IP and ADB private FQDN from Terraform output, renders `ansible/inventory.ini`, then runs the Ansible `socks5` role. The role installs and hardens the `danted` SOCKS5 daemon, restricts ingress to `CLIENT_CIDR`, and restricts egress to the ADB private endpoint on port 1522.

---

## 6. Fetch the ADB wallet

```bash
python manage.py wallet fetch
```

Downloads a fresh wallet ZIP from OCI, unzips it into `wallet/`, and sets all files to `chmod 600`.

> **Wallet freshness requirement:** wallets carry DigiCert root certificates. Wallets minted before 28 Jan 2026 carry G1 roots which are not trusted after 15 Apr 2026; current wallets use G2 roots. Always fetch a current wallet — never reuse stale wallet material. See the troubleshooting section if you see certificate errors.

---

## 7. Verify connectivity

```bash
python manage.py socks status
```

This checks that the jump host is reachable on port 1080. Expected output: `<IP>:1080 reachable`.

You can also independently verify the SOCKS relay carries TCP to the ADB port:

```bash
curl -v --socks5-hostname <JUMPHOST_IP>:1080 telnet://<ADB_PRIVATE_FQDN>:1522
```

A successful TCP handshake (then a closed connection) confirms the relay and remote DNS resolution both work.

---

## 8. Build and run the application

```bash
python manage.py build
```

Runs `./gradlew bootJar` inside `app/`. The output JAR lands in `app/build/libs/`.

```bash
python manage.py run
```

Launches the JAR with all environment variables from `.env` and Terraform output. The app binds on port 8080.

```bash
python manage.py health
```

Calls `GET localhost:8080/actuator/health` and exits non-zero if the status is not `UP`. A successful response includes the database sub-check, which confirms an end-to-end query executed through the SOCKS5 proxy.

---

## 9. Optional: Bastion demo path

If you provisioned with `--enable-bastion`, you can route JDBC through OCI Bastion instead of the jump host. The Bastion path is ephemeral (3-hour hard session TTL) and is intended for demo/ad-hoc access only.

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

## 10. Teardown

```bash
python manage.py clean --destroy
```

Removes `wallet/` contents and runs `terraform destroy`.

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

All files under `wallet/` must be `chmod 600` and owned by the user running the JVM. `manage.py wallet fetch` sets this automatically. If you unzip manually, run:

```bash
chmod 600 wallet/*
```

### DigiCert G1 vs G2 wallet roots

Wallets minted before 28 Jan 2026 carry DigiCert G1 roots. G1 roots are not trusted after 15 Apr 2026. If you see `PKIX path building failed`, `certificate unknown`, or similar TLS errors, your wallet is stale. Fix: run `python manage.py wallet fetch` to pull a current (G2) wallet.

### danted directive names across versions

Older `danted` packages use `method:` while newer ones use `socksmethod:` (within a `socks pass {}` block). Check the installed version with `danted -v` and match the directive to that version's `danted.conf` man page. The Ansible role renders config for the installed version.

### `tnsping` does not traverse SOCKS

`tnsping` uses the Oracle thick client (C libraries), which does not honour `oracle.net.socks*` JDBC properties. A failing `tnsping` alongside a working JDBC connection is expected and normal — use `python manage.py health` or the `curl --socks5-hostname` test in step 7 to verify connectivity instead.
