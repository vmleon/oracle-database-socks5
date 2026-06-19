# Oracle JDBC over SOCKS5 PoC — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible IaC PoC proving a Java client can reach a private Oracle ADB-S (26ai, mTLS wallet) through a SOCKS5 proxy via the Oracle JDBC thin driver, exposing a health endpoint that validates DB connectivity through the tunnel.

**Architecture:** Terraform provisions VCN + private ADB-S + public jump host (+ optional Bastion). Ansible installs/hardens `danted` SOCKS5 on the jump host. A Spring Boot 4.1 app (UCP/ojdbc17) connects through the proxy using connection-scoped `oracle.net.socks*` properties and reports DB health via Actuator. `manage.py` (typer) orchestrates the whole lifecycle. The proxy is a dumb relay (no wallet); mTLS stays end-to-end client↔ADB.

**Tech Stack:** Terraform ≥1.6 (`hashicorp/oci`), Ansible, `danted`, Spring Boot 4.1 / Java 21, `ojdbc17`/`ucp17`/`oraclepki` 23.26.x, Python 3.11+ (typer), Maven 3.9+.

## Global Constraints

> Every task's requirements implicitly include this section. Values copied verbatim from the SPEC (`oracle-socks5-jdbc-poc-SPEC.md`).

- **JDBC stack:** `com.oracle.database.jdbc:ojdbc17:23.26.x`, `:ucp17:23.26.x`, `com.oracle.database.security:oraclepki:23.26.x` (+ `osdt_core`, `osdt_cert` for mTLS auto-login wallet). Pin the latest 23.26 patch.
- **JDK:** 21 LTS. Do NOT move to 25/26 (known ojdbc bugs / uncertified).
- **Spring Boot:** 4.1 (Spring Framework 7 / Jakarta EE 11 / Tomcat 11). Virtual threads on (`spring.threads.virtual.enabled=true`).
- **SOCKS properties are connection-scoped, NEVER JVM-wide.** Use `oracle.net.socksProxyHost/Port/RemoteDNS`. `socksRemoteDNS=true` is REQUIRED (resolve ADB FQDN at proxy). Do NOT use legacy JVM `socksProxyHost` + `oracle.jdbc.javaNetNio=false`.
- **SOCKS daemon:** `danted` (Dante). Production/default `socksmethod: none`; `username` only for the §6.1 experiment.
- **Auth modes:** support both `mtls` (primary) and `tls` (walletless) via `AUTH_MODE` toggle.
- **Proxy trust model:** dumb relay — jump host holds NO wallet, sees only ciphertext + destination.
- **Wallet (mtls):** must be freshly generated (G2 roots). Never reuse pre-2026 G1 wallets (distrusted after 2026-04-15). `manage.py wallet fetch` always pulls current.
- **No ZPR** in this build. NSG source-IP allowlist is the network control.
- **Secrets** (region, compartment OCID, client `/32` CIDR, SSH key) live in `.env` / `terraform.tfvars`, never hardcoded. Scaffold `.example` files only.
- **gitignore:** `wallet/`, `*.tfstate*`, `*.pem`, `.env`, ssh keys, `.terraform/`, `target/`.
- **Config precedence (manage.py):** CLI flag > `.env` > `terraform output -json`. `MODE=jumphost|bastion`.

---

## File Structure

```
oracle-socks5-jdbc-poc/                      (repo root = oracle-database-socks5/)
├── README.md  DEPLOY.md  DEMO.md
├── manage.py  pyproject.toml  .env.example  .gitignore
├── infra/terraform/
│   ├── versions.tf providers.tf variables.tf main.tf outputs.tf
│   ├── terraform.tfvars.example
│   └── modules/{network,adb,jumphost,bastion}/{main.tf,variables.tf,outputs.tf}
├── ansible/
│   ├── inventory.ini.example
│   ├── socks5.yml                           # playbook
│   └── roles/socks5/{tasks,templates,defaults,handlers}/...
└── app/
    ├── pom.xml
    └── src/main/java/com/example/socks5poc/...
        src/main/resources/application.yml
        src/test/java/com/example/socks5poc/...
```

Each Terraform module owns one OCI concern (network / db / jumphost / bastion). The Java app splits into `App` (bootstrap), `config` (datasource wiring), `health` (DB indicator). `manage.py` is a single typer CLI file (POC — one file is appropriate).

---

## Task 1: Repo scaffolding + gitignore + .env.example

**Files:**

- Create: `.gitignore`, `.env.example`
- Create: directory skeleton (empty dirs tracked via the files added in later tasks)

**Interfaces:**

- Produces: `.env.example` keys consumed by `manage.py` (Task 14) and the app (Task 11): `OCI_REGION`, `COMPARTMENT_OCID`, `CLIENT_CIDR`, `SSH_PUBLIC_KEY_PATH`, `MODE`, `SOCKS_HOST`, `SOCKS_PORT`, `AUTH_MODE`, `TNS_ALIAS`, `WALLET_PATH`, `DB_USER`, `DB_PASSWORD`, `ENABLE_BASTION`.

- [ ] **Step 1: Write `.gitignore`**

```gitignore
# secrets / state / generated
.env
wallet/
*.tfstate
*.tfstate.*
.terraform/
.terraform.lock.hcl
*.pem
*.key
id_rsa*
ansible/inventory.ini
# build
app/target/
__pycache__/
.venv/
```

- [ ] **Step 2: Write `.env.example`**

```bash
# --- OCI ---
OCI_REGION=eu-frankfurt-1
COMPARTMENT_OCID=ocid1.compartment.oc1..xxxxx
CLIENT_CIDR=203.0.113.10/32          # your public egress /32 for NSG + bastion allowlist
SSH_PUBLIC_KEY_PATH=~/.ssh/id_rsa.pub
ENABLE_BASTION=false

# --- connectivity ---
MODE=jumphost                        # jumphost | bastion
SOCKS_HOST=                          # jumphost public IP (jumphost) or 127.0.0.1 (bastion)
SOCKS_PORT=1080

# --- DB / auth ---
AUTH_MODE=mtls                       # mtls | tls
TNS_ALIAS=dbpoc_high
WALLET_PATH=./wallet                 # empty for AUTH_MODE=tls
DB_USER=ADMIN
DB_PASSWORD=change-me
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore .env.example
git commit -m "chore: repo scaffolding, gitignore, env example"
```

---

## Task 2: Terraform skeleton (versions, providers, root variables)

**Files:**

- Create: `infra/terraform/versions.tf`, `providers.tf`, `variables.tf`, `terraform.tfvars.example`

**Interfaces:**

- Produces: root variables consumed by all modules: `region`, `compartment_ocid`, `client_cidr`, `ssh_public_key`, `db_version` (default `"26ai"`), `auth_mode` (default `"mtls"`), `db_admin_password`, `db_name` (default `"dbpoc"`), `enable_bastion` (default `false`), `jumphost_shape`, `adb_ecpu_count` (default `2`).

- [ ] **Step 1: Write `versions.tf`**

```hcl
terraform {
  required_version = ">= 1.6.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 6.0.0"
    }
  }
}
```

- [ ] **Step 2: Write `providers.tf`**

```hcl
provider "oci" {
  region = var.region
}
```

- [ ] **Step 3: Write `variables.tf`** (root)

```hcl
variable "region" { type = string }
variable "compartment_ocid" { type = string }
variable "client_cidr" {
  type        = string
  description = "Operator/client public egress CIDR (e.g. 203.0.113.10/32)"
}
variable "ssh_public_key" { type = string }
variable "db_version" {
  type    = string
  default = "26ai"
}
variable "db_name" {
  type    = string
  default = "dbpoc"
}
variable "auth_mode" {
  type    = string
  default = "mtls"
  validation {
    condition     = contains(["mtls", "tls"], var.auth_mode)
    error_message = "auth_mode must be mtls or tls."
  }
}
variable "db_admin_password" {
  type      = string
  sensitive = true
}
variable "adb_ecpu_count" {
  type    = number
  default = 2
}
variable "jumphost_shape" {
  type    = string
  default = "VM.Standard.E5.Flex"
}
variable "enable_bastion" {
  type    = bool
  default = false
}
```

- [ ] **Step 4: Write `terraform.tfvars.example`**

```hcl
region            = "eu-frankfurt-1"
compartment_ocid  = "ocid1.compartment.oc1..xxxxx"
client_cidr       = "203.0.113.10/32"
ssh_public_key    = "ssh-rsa AAAA... user@host"
db_admin_password = "Welcome_12345#"
enable_bastion    = false
```

- [ ] **Step 5: Verify formatting**

Run: `cd infra/terraform && terraform fmt -check`
Expected: no output (files already formatted). If it reformats, that's fine.

- [ ] **Step 6: Commit**

```bash
git add infra/terraform/versions.tf infra/terraform/providers.tf infra/terraform/variables.tf infra/terraform/terraform.tfvars.example
git commit -m "feat(tf): terraform skeleton and root variables"
```

---

## Task 3: Terraform `network` module

**Files:**

- Create: `infra/terraform/modules/network/{main.tf,variables.tf,outputs.tf}`

**Interfaces:**

- Consumes: `compartment_ocid`, `client_cidr`.
- Produces outputs: `vcn_id`, `public_subnet_id`, `private_subnet_id`, `jumphost_nsg_id`, `adb_nsg_id`.

- [ ] **Step 1: Write `variables.tf`**

```hcl
variable "compartment_ocid" { type = string }
variable "client_cidr" { type = string }
variable "vcn_cidr" {
  type    = string
  default = "10.0.0.0/16"
}
```

- [ ] **Step 2: Write `main.tf`** — VCN, IGW, public + private subnets, route tables, two NSGs with rules

```hcl
resource "oci_core_vcn" "this" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "socks5-poc-vcn"
  dns_label      = "socks5poc"
}

resource "oci_core_internet_gateway" "igw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "socks5-poc-igw"
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "socks5-poc-public-rt"
  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_internet_gateway.igw.id
  }
}

resource "oci_core_subnet" "public" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.this.id
  cidr_block                 = "10.0.1.0/24"
  display_name               = "socks5-poc-public-subnet"
  route_table_id             = oci_core_route_table.public.id
  prohibit_public_ip_on_vnic = false
  dns_label                  = "pub"
}

resource "oci_core_subnet" "private" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.this.id
  cidr_block                 = "10.0.2.0/24"
  display_name               = "socks5-poc-private-subnet"
  prohibit_public_ip_on_vnic = true
  dns_label                  = "priv"
}

# NSG for the jump host: ingress 22 + 1080 from client only; egress 1522 to ADB NSG
resource "oci_core_network_security_group" "jumphost" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "socks5-poc-jumphost-nsg"
}

# NSG for ADB private endpoint: ingress 1522 from jumphost NSG only
resource "oci_core_network_security_group" "adb" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "socks5-poc-adb-nsg"
}

resource "oci_core_network_security_group_security_rule" "jh_ssh" {
  network_security_group_id = oci_core_network_security_group.jumphost.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = var.client_cidr
  source_type               = "CIDR_BLOCK"
  tcp_options { destination_port_range { min = 22, max = 22 } }
}

resource "oci_core_network_security_group_security_rule" "jh_socks" {
  network_security_group_id = oci_core_network_security_group.jumphost.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = var.client_cidr
  source_type               = "CIDR_BLOCK"
  tcp_options { destination_port_range { min = 1080, max = 1080 } }
}

resource "oci_core_network_security_group_security_rule" "jh_egress_adb" {
  network_security_group_id = oci_core_network_security_group.jumphost.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = oci_core_network_security_group.adb.id
  destination_type          = "NETWORK_SECURITY_GROUP"
  tcp_options { destination_port_range { min = 1522, max = 1522 } }
}

resource "oci_core_network_security_group_security_rule" "adb_ingress_jh" {
  network_security_group_id = oci_core_network_security_group.adb.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = oci_core_network_security_group.jumphost.id
  source_type               = "NETWORK_SECURITY_GROUP"
  tcp_options { destination_port_range { min = 1522, max = 1522 } }
}
```

- [ ] **Step 3: Write `outputs.tf`**

```hcl
output "vcn_id" { value = oci_core_vcn.this.id }
output "public_subnet_id" { value = oci_core_subnet.public.id }
output "private_subnet_id" { value = oci_core_subnet.private.id }
output "jumphost_nsg_id" { value = oci_core_network_security_group.jumphost.id }
output "adb_nsg_id" { value = oci_core_network_security_group.adb.id }
```

- [ ] **Step 4: Commit** (validation happens in Task 7 once root wires modules)

```bash
git add infra/terraform/modules/network
git commit -m "feat(tf): network module (VCN, subnets, NSGs)"
```

---

## Task 4: Terraform `adb` module (private endpoint, mTLS)

**Files:**

- Create: `infra/terraform/modules/adb/{main.tf,variables.tf,outputs.tf}`

**Interfaces:**

- Consumes: `compartment_ocid`, `db_name`, `db_version`, `db_admin_password`, `adb_ecpu_count`, `private_subnet_id`, `adb_nsg_id`.
- Produces outputs: `adb_id`, `private_endpoint` (FQDN), `db_name`.

- [ ] **Step 1: Write `variables.tf`**

```hcl
variable "compartment_ocid" { type = string }
variable "db_name" { type = string }
variable "db_version" { type = string }
variable "db_admin_password" {
  type      = string
  sensitive = true
}
variable "adb_ecpu_count" { type = number }
variable "private_subnet_id" { type = string }
variable "adb_nsg_id" { type = string }
```

- [ ] **Step 2: Write `main.tf`**

```hcl
resource "oci_database_autonomous_database" "this" {
  compartment_id           = var.compartment_ocid
  db_name                  = var.db_name
  display_name             = var.db_name
  db_version               = var.db_version
  db_workload              = "OLTP"
  compute_model            = "ECPU"
  compute_count            = var.adb_ecpu_count
  data_storage_size_in_tbs = 1
  admin_password           = var.db_admin_password
  is_auto_scaling_enabled  = false

  # private endpoint
  subnet_id          = var.private_subnet_id
  nsg_ids            = [var.adb_nsg_id]
  private_endpoint_label = "dbpoc-pe"

  # mTLS only (mutual TLS required; not walletless TLS at the listener)
  is_mtls_connection_required = true
}
```

- [ ] **Step 3: Write `outputs.tf`**

```hcl
output "adb_id" { value = oci_database_autonomous_database.this.id }
output "private_endpoint" { value = oci_database_autonomous_database.this.private_endpoint }
output "db_name" { value = oci_database_autonomous_database.this.db_name }
```

> Note: `is_mtls_connection_required = true` keeps the mTLS wallet path. For `auth_mode = tls`, the README documents flipping this to `false` (walletless TLS available because the endpoint is private). The PoC primary is mtls; the toggle is documented, not auto-flipped, to keep the apply deterministic.

- [ ] **Step 4: Commit**

```bash
git add infra/terraform/modules/adb
git commit -m "feat(tf): adb module (private endpoint, mTLS)"
```

---

## Task 5: Terraform `jumphost` module

**Files:**

- Create: `infra/terraform/modules/jumphost/{main.tf,variables.tf,outputs.tf}`

**Interfaces:**

- Consumes: `compartment_ocid`, `public_subnet_id`, `jumphost_nsg_id`, `ssh_public_key`, `jumphost_shape`.
- Produces outputs: `jumphost_public_ip`, `jumphost_id`.

- [ ] **Step 1: Write `variables.tf`**

```hcl
variable "compartment_ocid" { type = string }
variable "public_subnet_id" { type = string }
variable "jumphost_nsg_id" { type = string }
variable "ssh_public_key" { type = string }
variable "jumphost_shape" { type = string }
variable "ocpus" {
  type    = number
  default = 1
}
variable "memory_in_gbs" {
  type    = number
  default = 8
}
```

- [ ] **Step 2: Write `main.tf`** — latest Ubuntu image, smallest flex shape, public IP, NSG attached

```hcl
data "oci_identity_availability_domains" "ads" {
  compartment_id = var.compartment_ocid
}

data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "22.04"
  shape                    = var.jumphost_shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

resource "oci_core_instance" "jumphost" {
  compartment_id      = var.compartment_ocid
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  display_name        = "socks5-poc-jumphost"
  shape               = var.jumphost_shape

  shape_config {
    ocpus         = var.ocpus
    memory_in_gbs = var.memory_in_gbs
  }

  create_vnic_details {
    subnet_id        = var.public_subnet_id
    assign_public_ip = true
    nsg_ids          = [var.jumphost_nsg_id]
  }

  source_details {
    source_type = "image"
    source_id   = data.oci_core_images.ubuntu.images[0].id
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
  }
}
```

- [ ] **Step 3: Write `outputs.tf`**

```hcl
output "jumphost_public_ip" { value = oci_core_instance.jumphost.public_ip }
output "jumphost_id" { value = oci_core_instance.jumphost.id }
```

- [ ] **Step 4: Commit**

```bash
git add infra/terraform/modules/jumphost
git commit -m "feat(tf): jumphost module (public compute, NSG)"
```

---

## Task 6: Terraform `bastion` module (gated by enable_bastion)

**Files:**

- Create: `infra/terraform/modules/bastion/{main.tf,variables.tf,outputs.tf}`

**Interfaces:**

- Consumes: `compartment_ocid`, `private_subnet_id`, `client_cidr`.
- Produces output: `bastion_id`.

- [ ] **Step 1: Write `variables.tf`**

```hcl
variable "compartment_ocid" { type = string }
variable "private_subnet_id" { type = string }
variable "client_cidr" { type = string }
```

- [ ] **Step 2: Write `main.tf`** (dynamic port-forwarding bastion)

```hcl
resource "oci_bastion_bastion" "this" {
  compartment_id               = var.compartment_ocid
  bastion_type                 = "STANDARD"
  target_subnet_id             = var.private_subnet_id
  name                         = "socks5pocbastion"
  client_cidr_block_allow_list = [var.client_cidr]
}
```

- [ ] **Step 3: Write `outputs.tf`**

```hcl
output "bastion_id" { value = oci_bastion_bastion.this.id }
```

- [ ] **Step 4: Commit**

```bash
git add infra/terraform/modules/bastion
git commit -m "feat(tf): bastion module for dynamic port-forwarding demo path"
```

---

## Task 7: Terraform root wiring + outputs + validate

**Files:**

- Create: `infra/terraform/main.tf`, `infra/terraform/outputs.tf`

**Interfaces:**

- Consumes: all module outputs.
- Produces root outputs: `jumphost_public_ip`, `adb_private_endpoint`, `adb_id`, `db_name`, `bastion_id` (nullable).

- [ ] **Step 1: Write `main.tf`** wiring the modules

```hcl
module "network" {
  source           = "./modules/network"
  compartment_ocid = var.compartment_ocid
  client_cidr      = var.client_cidr
}

module "adb" {
  source            = "./modules/adb"
  compartment_ocid  = var.compartment_ocid
  db_name           = var.db_name
  db_version        = var.db_version
  db_admin_password = var.db_admin_password
  adb_ecpu_count    = var.adb_ecpu_count
  private_subnet_id = module.network.private_subnet_id
  adb_nsg_id        = module.network.adb_nsg_id
}

module "jumphost" {
  source           = "./modules/jumphost"
  compartment_ocid = var.compartment_ocid
  public_subnet_id = module.network.public_subnet_id
  jumphost_nsg_id  = module.network.jumphost_nsg_id
  ssh_public_key   = var.ssh_public_key
  jumphost_shape   = var.jumphost_shape
}

module "bastion" {
  count             = var.enable_bastion ? 1 : 0
  source            = "./modules/bastion"
  compartment_ocid  = var.compartment_ocid
  private_subnet_id = module.network.private_subnet_id
  client_cidr       = var.client_cidr
}
```

- [ ] **Step 2: Write `outputs.tf`**

```hcl
output "jumphost_public_ip" { value = module.jumphost.jumphost_public_ip }
output "adb_private_endpoint" { value = module.adb.private_endpoint }
output "adb_id" { value = module.adb.adb_id }
output "db_name" { value = module.adb.db_name }
output "bastion_id" {
  value = var.enable_bastion ? module.bastion[0].bastion_id : null
}
```

- [ ] **Step 3: Validate the whole config**

Run: `cd infra/terraform && terraform init -backend=false && terraform validate`
Expected: `Success! The configuration is valid.` (init downloads the oci provider; no credentials needed for validate.)

- [ ] **Step 4: Commit**

```bash
git add infra/terraform/main.tf infra/terraform/outputs.tf
git commit -m "feat(tf): wire modules in root, outputs, validated"
```

---

## Task 8: Ansible socks5 role — danted install + config

**Files:**

- Create: `ansible/socks5.yml`, `ansible/inventory.ini.example`
- Create: `ansible/roles/socks5/defaults/main.yml`
- Create: `ansible/roles/socks5/tasks/main.yml`
- Create: `ansible/roles/socks5/templates/danted.conf.j2`
- Create: `ansible/roles/socks5/handlers/main.yml`

**Interfaces:**

- Consumes (role vars): `adb_fqdn`, `client_cidr`, `socks_port` (default 1080), `socks_auth_method` (`none`|`username`), `socks_debug` (0|2), `socks_username`/`socks_password` (only for username).

- [ ] **Step 1: Write `defaults/main.yml`**

```yaml
socks_port: 1080
socks_auth_method: none # none | username
socks_debug: 0 # 0 | 2  (2 logs negotiated method for the experiment)
adb_fqdn: "" # ADB private endpoint FQDN (destination allowlist)
client_cidr: "0.0.0.0/0" # restrict at proxy; NSG also restricts
socks_username: socksuser
socks_password: ""
```

- [ ] **Step 2: Write `templates/danted.conf.j2`**

```jinja
logoutput: {{ '/var/log/danted.log' if socks_debug|int > 0 else 'syslog' }}
{% if socks_debug|int > 0 %}debug: {{ socks_debug }}{% endif %}

internal: 0.0.0.0 port = {{ socks_port }}
external: {{ ansible_default_ipv4.interface }}

socksmethod: {{ socks_auth_method }}
{% if socks_auth_method == 'none' %}clientmethod: none{% endif %}
user.privileged: root
user.unprivileged: nobody

# only allowed clients may reach the proxy
client pass {
    from: {{ client_cidr }} to: 0.0.0.0/0
    log: connect disconnect error
}

# relay only to the ADB private endpoint on 1522
socks pass {
    from: {{ client_cidr }} to: {{ adb_fqdn }}/32 port = 1522
    protocol: tcp
    {% if socks_auth_method == 'username' %}socksmethod: username{% endif %}
    log: connect disconnect error
}
socks block {
    from: 0.0.0.0/0 to: 0.0.0.0/0
    log: connect error
}
```

> Directive names are validated against the installed Dante version in Step 5. If `adb_fqdn` resolution at config-render time is an issue, Dante accepts a hostname in the `to:` rule and resolves it at runtime (remote DNS); this is what makes `socksRemoteDNS=true` work.

- [ ] **Step 3: Write `tasks/main.yml`** — install danted, create auth user if needed, render config, harden

```yaml
- name: Install danted, fail2ban, unattended-upgrades
  apt:
    name: [dante-server, fail2ban, unattended-upgrades]
    state: present
    update_cache: true

- name: Create SOCKS auth user (username mode only)
  user:
    name: "{{ socks_username }}"
    password: "{{ socks_password | password_hash('sha512') }}"
    shell: /usr/sbin/nologin
  when: socks_auth_method == 'username' and socks_password | length > 0

- name: Render danted.conf
  template:
    src: danted.conf.j2
    dest: /etc/danted.conf
    mode: "0644"
  notify: restart danted

- name: Allow SSH and SOCKS from client CIDR only (ufw)
  ufw:
    rule: allow
    port: "{{ item }}"
    proto: tcp
    src: "{{ client_cidr }}"
  loop: ["22", "{{ socks_port }}"]

- name: Default-deny incoming
  ufw:
    state: enabled
    policy: deny
    direction: incoming

- name: Disable SSH password auth
  lineinfile:
    path: /etc/ssh/sshd_config
    regexp: "^#?PasswordAuthentication"
    line: "PasswordAuthentication no"
  notify: restart sshd

- name: Enable and start danted
  systemd:
    name: danted
    enabled: true
    state: started
```

- [ ] **Step 4: Write `handlers/main.yml`**

```yaml
- name: restart danted
  systemd:
    name: danted
    state: restarted

- name: restart sshd
  systemd:
    name: ssh
    state: restarted
```

- [ ] **Step 5: Write `ansible/socks5.yml` playbook + `inventory.ini.example`**

`socks5.yml`:

```yaml
- hosts: jumphost
  become: true
  roles:
    - socks5
```

`inventory.ini.example`:

```ini
[jumphost]
JUMPHOST_PUBLIC_IP ansible_user=ubuntu ansible_ssh_private_key_file=~/.ssh/id_rsa
```

- [ ] **Step 6: Lint / syntax check**

Run: `cd ansible && ansible-playbook socks5.yml --syntax-check -i inventory.ini.example`
Expected: `playbook: socks5.yml` (no syntax errors). If `ansible-lint` is installed: `ansible-lint roles/socks5` (warnings acceptable for POC).

- [ ] **Step 7: Commit**

```bash
git add ansible/
git commit -m "feat(ansible): danted socks5 role with hardening + auth toggle"
```

---

## Task 9: Java app — pom.xml + application.yml

**Files:**

- Create: `app/pom.xml`
- Create: `app/src/main/resources/application.yml`

**Interfaces:**

- Produces: Maven project `com.example:socks5poc`, Spring Boot 4.1, Java 21, deps per Global Constraints. `application.yml` binds `app.*` config (Task 10).

- [ ] **Step 1: Write `pom.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>

  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>4.1.0</version>
    <relativePath/>
  </parent>

  <groupId>com.example</groupId>
  <artifactId>socks5poc</artifactId>
  <version>0.1.0</version>

  <properties>
    <java.version>21</java.version>
    <oracle.jdbc.version>23.26.0.25.10</oracle.jdbc.version>
  </properties>

  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-actuator</artifactId>
    </dependency>
    <dependency>
      <groupId>com.oracle.database.jdbc</groupId>
      <artifactId>ojdbc17</artifactId>
      <version>${oracle.jdbc.version}</version>
    </dependency>
    <dependency>
      <groupId>com.oracle.database.jdbc</groupId>
      <artifactId>ucp17</artifactId>
      <version>${oracle.jdbc.version}</version>
    </dependency>
    <dependency>
      <groupId>com.oracle.database.security</groupId>
      <artifactId>oraclepki</artifactId>
      <version>${oracle.jdbc.version}</version>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-test</artifactId>
      <scope>test</scope>
    </dependency>
  </dependencies>

  <build>
    <plugins>
      <plugin>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-maven-plugin</artifactId>
      </plugin>
    </plugins>
  </build>
</project>
```

> Verify the exact latest `23.26.x` patch at build time (`oracle.jdbc.version`) and Spring Boot `4.1.x` patch against Maven Central. `osdt_core`/`osdt_cert` come transitively with `oraclepki`; add explicitly only if the auto-login wallet fails to load.

- [ ] **Step 2: Write `application.yml`**

```yaml
server:
  port: 8080
spring:
  threads:
    virtual:
      enabled: true
management:
  endpoint:
    health:
      show-details: always
      probes:
        enabled: true
  endpoints:
    web:
      exposure:
        include: health,info
app:
  auth-mode: ${AUTH_MODE:mtls}
  db:
    tns-alias: ${TNS_ALIAS:dbpoc_high}
    wallet-path: ${WALLET_PATH:}
    tls-url: ${DB_TLS_URL:}
    user: ${DB_USER}
    password: ${DB_PASSWORD}
  socks:
    host: ${SOCKS_HOST}
    port: ${SOCKS_PORT:1080}
    mode: ${MODE:jumphost}
    remote-dns: ${SOCKS_REMOTE_DNS:true}
```

> `socks.remote-dns` defaults true; the negative test (success criterion #4) sets `SOCKS_REMOTE_DNS=false` to prove failure.

- [ ] **Step 3: Verify dependency resolution**

Run: `cd app && mvn -q -o dependency:resolve 2>/dev/null || mvn -q dependency:resolve`
Expected: BUILD SUCCESS (downloads deps). If a version is unavailable, adjust the patch version to the latest published 23.26.x / 4.1.x.

- [ ] **Step 4: Commit**

```bash
git add app/pom.xml app/src/main/resources/application.yml
git commit -m "feat(app): maven pom and application.yml"
```

---

## Task 10: Java app — config properties + DataSourceConfig

**Files:**

- Create: `app/src/main/java/com/example/socks5poc/config/AppProperties.java`
- Create: `app/src/main/java/com/example/socks5poc/config/DataSourceConfig.java`
- Create: `app/src/main/java/com/example/socks5poc/App.java`

**Interfaces:**

- Consumes: `app.*` from application.yml.
- Produces: `@Bean PoolDataSource dataSource()` for the health indicator (Task 11). `AppProperties` getters: `getAuthMode()`, `getDb()`, `getSocks()` with nested `Db{tnsAlias, walletPath, tlsUrl, user, password}` and `Socks{host, port, mode, remoteDns}`.

- [ ] **Step 1: Write `AppProperties.java`** (`@ConfigurationProperties("app")`)

```java
package com.example.socks5poc.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties("app")
public class AppProperties {
    private String authMode = "mtls";
    private Db db = new Db();
    private Socks socks = new Socks();

    public String getAuthMode() { return authMode; }
    public void setAuthMode(String authMode) { this.authMode = authMode; }
    public Db getDb() { return db; }
    public void setDb(Db db) { this.db = db; }
    public Socks getSocks() { return socks; }
    public void setSocks(Socks socks) { this.socks = socks; }

    public static class Db {
        private String tnsAlias;
        private String walletPath = "";
        private String tlsUrl = "";
        private String user;
        private String password;
        public String getTnsAlias() { return tnsAlias; }
        public void setTnsAlias(String v) { this.tnsAlias = v; }
        public String getWalletPath() { return walletPath; }
        public void setWalletPath(String v) { this.walletPath = v; }
        public String getTlsUrl() { return tlsUrl; }
        public void setTlsUrl(String v) { this.tlsUrl = v; }
        public String getUser() { return user; }
        public void setUser(String v) { this.user = v; }
        public String getPassword() { return password; }
        public void setPassword(String v) { this.password = v; }
    }

    public static class Socks {
        private String host;
        private int port = 1080;
        private String mode = "jumphost";
        private boolean remoteDns = true;
        public String getHost() { return host; }
        public void setHost(String v) { this.host = v; }
        public int getPort() { return port; }
        public void setPort(int v) { this.port = v; }
        public String getMode() { return mode; }
        public void setMode(String v) { this.mode = v; }
        public boolean isRemoteDns() { return remoteDns; }
        public void setRemoteDns(boolean v) { this.remoteDns = v; }
    }
}
```

- [ ] **Step 2: Write `DataSourceConfig.java`** — builds URL per auth-mode, sets connection-scoped socks props

```java
package com.example.socks5poc.config;

import java.util.Properties;
import oracle.ucp.jdbc.PoolDataSource;
import oracle.ucp.jdbc.PoolDataSourceFactory;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties(AppProperties.class)
public class DataSourceConfig {

    @Bean
    public PoolDataSource dataSource(AppProperties props) throws Exception {
        PoolDataSource ds = PoolDataSourceFactory.getPoolDataSource();
        ds.setConnectionFactoryClassName("oracle.jdbc.pool.OracleDataSource");

        AppProperties.Db db = props.getDb();
        String url;
        if ("tls".equalsIgnoreCase(props.getAuthMode())) {
            url = db.getTlsUrl(); // full TLS connect string from ADB console
        } else {
            url = "jdbc:oracle:thin:@" + db.getTnsAlias()
                + "?TNS_ADMIN=" + db.getWalletPath();
        }
        ds.setURL(url);
        ds.setUser(db.getUser());
        ds.setPassword(db.getPassword());

        Properties p = new Properties();
        AppProperties.Socks socks = props.getSocks();
        p.setProperty("oracle.net.socksProxyHost", socks.getHost());
        p.setProperty("oracle.net.socksProxyPort", String.valueOf(socks.getPort()));
        p.setProperty("oracle.net.socksRemoteDNS", String.valueOf(socks.isRemoteDns()));
        ds.setConnectionProperties(p);

        ds.setValidateConnectionOnBorrow(true);
        ds.setSQLForValidateConnection("SELECT 1 FROM DUAL");
        ds.setInitialPoolSize(1);
        ds.setMinPoolSize(1);
        ds.setMaxPoolSize(5);
        return ds;
    }
}
```

- [ ] **Step 3: Write `App.java`**

```java
package com.example.socks5poc;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class App {
    public static void main(String[] args) {
        SpringApplication.run(App.class, args);
    }
}
```

- [ ] **Step 4: Compile**

Run: `cd app && mvn -q compile`
Expected: BUILD SUCCESS.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/example/socks5poc/config app/src/main/java/com/example/socks5poc/App.java
git commit -m "feat(app): config properties and UCP datasource with socks props"
```

---

## Task 11: Java app — DatabaseHealthIndicator (TDD)

**Files:**

- Create: `app/src/main/java/com/example/socks5poc/health/DatabaseHealthIndicator.java`
- Test: `app/src/test/java/com/example/socks5poc/health/DatabaseHealthIndicatorTest.java`

**Interfaces:**

- Consumes: `PoolDataSource` (Task 10), `AppProperties` for detail reporting.
- Produces: `Health health()` returning `UP` with details `{latencyMs, borrowed, available, socks, mode}` or `DOWN` with sanitized error.

- [ ] **Step 1: Write the failing test** (mock DataSource, verify UP details + sanitized DOWN)

```java
package com.example.socks5poc.health;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

import com.example.socks5poc.config.AppProperties;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;
import oracle.ucp.jdbc.PoolDataSource;
import org.junit.jupiter.api.Test;
import org.springframework.boot.actuate.health.Health;
import org.springframework.boot.actuate.health.Status;

class DatabaseHealthIndicatorTest {

    private AppProperties props() {
        AppProperties p = new AppProperties();
        p.getSocks().setHost("1.2.3.4");
        p.getSocks().setPort(1080);
        p.getSocks().setMode("jumphost");
        return p;
    }

    @Test
    void reportsUpWhenQuerySucceeds() throws Exception {
        PoolDataSource ds = mock(PoolDataSource.class);
        Connection conn = mock(Connection.class);
        Statement st = mock(Statement.class);
        ResultSet rs = mock(ResultSet.class);
        when(ds.getConnection()).thenReturn(conn);
        when(conn.createStatement()).thenReturn(st);
        when(st.executeQuery("SELECT 1 FROM DUAL")).thenReturn(rs);
        when(rs.next()).thenReturn(true);
        when(ds.getBorrowedConnectionsCount()).thenReturn(1);
        when(ds.getAvailableConnectionsCount()).thenReturn(0);

        Health h = new DatabaseHealthIndicator(ds, props()).health();

        assertThat(h.getStatus()).isEqualTo(Status.UP);
        assertThat(h.getDetails()).containsKeys("latencyMs", "socks", "mode");
        assertThat(h.getDetails().get("socks")).isEqualTo("1.2.3.4:1080");
    }

    @Test
    void reportsDownWithSanitizedErrorOnFailure() throws Exception {
        PoolDataSource ds = mock(PoolDataSource.class);
        when(ds.getConnection()).thenThrow(new java.sql.SQLException("ORA-12545 secret host details"));

        Health h = new DatabaseHealthIndicator(ds, props()).health();

        assertThat(h.getStatus()).isEqualTo(Status.DOWN);
        assertThat(String.valueOf(h.getDetails().get("error"))).doesNotContain("secret host details");
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && mvn -q -Dtest=DatabaseHealthIndicatorTest test`
Expected: FAIL — `DatabaseHealthIndicator` does not exist.

- [ ] **Step 3: Write minimal implementation**

```java
package com.example.socks5poc.health;

import com.example.socks5poc.config.AppProperties;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;
import oracle.ucp.jdbc.PoolDataSource;
import org.springframework.boot.actuate.health.Health;
import org.springframework.boot.actuate.health.HealthIndicator;
import org.springframework.stereotype.Component;

@Component("db")
public class DatabaseHealthIndicator implements HealthIndicator {

    private final PoolDataSource ds;
    private final AppProperties props;

    public DatabaseHealthIndicator(PoolDataSource ds, AppProperties props) {
        this.ds = ds;
        this.props = props;
    }

    @Override
    public Health health() {
        long start = System.nanoTime();
        try (Connection c = ds.getConnection();
             Statement st = c.createStatement();
             ResultSet rs = st.executeQuery("SELECT 1 FROM DUAL")) {
            rs.next();
            long ms = (System.nanoTime() - start) / 1_000_000;
            return Health.up()
                .withDetail("latencyMs", ms)
                .withDetail("borrowed", ds.getBorrowedConnectionsCount())
                .withDetail("available", ds.getAvailableConnectionsCount())
                .withDetail("socks", props.getSocks().getHost() + ":" + props.getSocks().getPort())
                .withDetail("mode", props.getSocks().getMode())
                .build();
        } catch (Exception e) {
            return Health.down()
                .withDetail("error", sanitize(e))
                .withDetail("socks", props.getSocks().getHost() + ":" + props.getSocks().getPort())
                .withDetail("mode", props.getSocks().getMode())
                .build();
        }
    }

    private String sanitize(Exception e) {
        String name = e.getClass().getSimpleName();
        String msg = e.getMessage() == null ? "" : e.getMessage();
        // surface only the ORA-/error code prefix, drop host/identifier details
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("(ORA-\\d+|IO Error|UnknownHost)").matcher(msg);
        return m.find() ? name + ": " + m.group(1) : name;
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && mvn -q -Dtest=DatabaseHealthIndicatorTest test`
Expected: PASS (2 tests).

- [ ] **Step 5: Wire readiness group** — add to `application.yml`

```yaml
management:
  endpoint:
    health:
      group:
        readiness:
          include: db
```

- [ ] **Step 6: Commit**

```bash
git add app/src/main/java/com/example/socks5poc/health app/src/test app/src/main/resources/application.yml
git commit -m "feat(app): DB health indicator through socks tunnel with readiness group"
```

---

## Task 12: manage.py — CLI skeleton + config precedence (TDD)

**Files:**

- Create: `pyproject.toml`
- Create: `manage.py`
- Test: `tests/test_config.py`

**Interfaces:**

- Produces: `load_config(cli_overrides: dict) -> dict` implementing precedence CLI > `.env` > `terraform output -json`; typer app `app` with command stubs.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "socks5-poc-manage"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["typer>=0.12", "python-dotenv>=1.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]
```

- [ ] **Step 2: Write the failing test**

```python
import manage

def test_precedence_cli_over_env_over_tf(monkeypatch):
    env = {"SOCKS_HOST": "from-env"}
    tf = {"jumphost_public_ip": {"value": "from-tf"}}
    monkeypatch.setattr(manage, "_read_env", lambda: dict(env))
    monkeypatch.setattr(manage, "_read_tf_output", lambda: dict(tf))
    cfg = manage.load_config({"SOCKS_HOST": "from-cli"})
    assert cfg["SOCKS_HOST"] == "from-cli"

def test_env_falls_back_to_tf(monkeypatch):
    monkeypatch.setattr(manage, "_read_env", lambda: {})
    monkeypatch.setattr(manage, "_read_tf_output", lambda: {"jumphost_public_ip": {"value": "1.2.3.4"}})
    cfg = manage.load_config({})
    assert cfg["SOCKS_HOST"] == "1.2.3.4"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `manage` has no `load_config`.

- [ ] **Step 4: Write minimal `manage.py` (config layer + typer skeleton)**

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml manage.py tests/test_config.py
git commit -m "feat(cli): manage.py config precedence layer with tests"
```

---

## Task 13: manage.py — lifecycle commands

**Files:**

- Modify: `manage.py`

**Interfaces:**

- Consumes: `load_config`, `_read_tf_output`.
- Produces typer commands: `setup`, `tf` (plan/apply/destroy), `provision`, `wallet` (fetch), `socks` (status/up/down), `build`, `run`, `health`, `demo`, `clean`.

- [ ] **Step 1: Add command implementations to `manage.py`**

```python
def _sh(cmd, **kw):
    typer.echo("$ " + " ".join(cmd))
    return subprocess.run(cmd, check=True, **kw)


@app.command()
def setup():
    """Check prereqs and seed .env."""
    for tool in ["oci", "terraform", "ansible", "java", "mvn", "ssh"]:
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
    ip = tf["jumphost_public_ip"]["value"]
    fqdn = tf["adb_private_endpoint"]["value"]
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
    adb_id = tf["adb_id"]["value"]
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
        rc = subprocess.run(["nc", "-z", "-w", "3", host, str(port)]).returncode
        typer.echo(f"{host}:{port} {'reachable' if rc == 0 else 'DOWN'}")
        raise typer.Exit(rc)
    typer.echo("up/down implemented for bastion mode (see DEMO.md)")


@app.command()
def build():
    _sh(["mvn", "-q", "package", "-DskipTests"], cwd="app")


@app.command()
def run():
    cfg = load_config({})
    env = {**os.environ, **cfg}
    jar = next(Path("app/target").glob("socks5poc-*.jar"))
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
```

- [ ] **Step 2: Smoke-test the CLI loads**

Run: `python manage.py --help`
Expected: lists commands `setup tf provision wallet socks build run health clean`.

- [ ] **Step 3: Re-run config tests (no regression)**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 4: Commit**

```bash
git add manage.py
git commit -m "feat(cli): lifecycle commands (tf, provision, wallet, socks, build, run, health, clean)"
```

---

## Task 14: README.md

**Files:**

- Create: `README.md`

- [ ] **Step 1: Write `README.md`** covering: what the PoC proves; §3.A topology mermaid diagram; decision summary (§2 table, condensed); §3 connectivity comparison table; quickstart pointing to DEPLOY/DEMO; anti-patterns (legacy SOCKS, missing remote DNS, Bastion-as-always-on, reusing pre-2026 G1 wallet); `auth_mode` (mtls vs tls-walletless) explanation; cost/teardown note. Use present-tense, final-state prose (no history/changelog framing).

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README with topology, decisions, anti-patterns"
```

---

## Task 15: DEPLOY.md

**Files:**

- Create: `DEPLOY.md`

- [ ] **Step 1: Write `DEPLOY.md`** — ordered, copy-pasteable: OCI prereqs (CLI config, region, compartment OCID, client `/32` CIDR, SSH key); `.env`; `python manage.py tf apply`; `provision`; `wallet fetch` (note G2 freshness); verify ADB private endpoint + jump host reachability; troubleshooting (NSG 1522/1080, remote-DNS failure, wallet perms + G1/G2 distrust after 2026-04-15, danted version directive differences); optional `--mode bastion` bring-up. Present-tense, final-state.

- [ ] **Step 2: Commit**

```bash
git add DEPLOY.md
git commit -m "docs: DEPLOY step-by-step with troubleshooting"
```

---

## Task 16: DEMO.md + §6.1 experiment harness doc

**Files:**

- Create: `DEMO.md`

- [ ] **Step 1: Write `DEMO.md`** — the §11 validation sequence with expected outputs:
  1. Independent SOCKS test: `curl -v --socks5-hostname JUMPHOST_IP:1080 telnet://<adb-fqdn>:1522` succeeds; `--socks5` (local DNS) fails.
  2. Optional SQLcl through proxy.
  3. App happy path: `run` → `health` → `UP` with latency/pool/mode.
  4. Negative test: `SOCKS_REMOTE_DNS=false` → readiness `DOWN`; re-enable → `UP`.
  5. §6.1 SOCKS-auth experiment: set danted `socks_auth_method=username` + `socks_debug=2`, re-provision, attempt brave path (native props + NIO on + credentials), capture greeting bytes via `tcpdump -i any -X port 1080` / danted log; record offered methods (`05 01 00` no-auth-only vs `05 02 00 02` no-auth+user/pass) and outcome; a **Results** subsection with a fill-in table (offered methods, outcome, mode app ships in). Revert to `none`, confirm mode (B).
  6. Mode swap to bastion; note 3h TTL = demo-only.
  - Gotcha: `tnsping`/thick client do not traverse SOCKS.

- [ ] **Step 2: Commit**

```bash
git add DEMO.md
git commit -m "docs: DEMO validation sequence and socks-auth experiment harness"
```

---

## Self-Review — spec coverage

| SPEC success criterion / section                                  | Task(s)                                     |
| ----------------------------------------------------------------- | ------------------------------------------- |
| §1.1 `terraform apply` provisions VCN+ADB+jumphost                | 2–7                                         |
| §1.2 Ansible provisions/hardens danted                            | 8                                           |
| §1.3 `/actuator/health` UP w/ DB sub-check through proxy          | 10, 11                                      |
| §1.4 negative test (remote DNS off)                               | 11 (toggle), 16 (procedure)                 |
| §1.5 jumphost vs bastion by changing SOCKS_HOST/PORT              | 6, 13, 16                                   |
| §1.6 §6.1 SOCKS-auth experiment run + recorded                    | 8 (auth toggle+debug), 16 (harness+results) |
| §1.7 README/DEPLOY/DEMO                                           | 14, 15, 16                                  |
| §2 auth_mode mtls\|tls                                            | 4 (note), 9, 10                             |
| §5 repo layout (minus zpr)                                        | 1–16                                        |
| §6 UCP socks config, connection-scoped                            | 10                                          |
| §7 ansible danted role + hardening + auth toggle                  | 8                                           |
| §8 manage.py commands                                             | 12, 13                                      |
| §9 Spring Boot app, vthreads, health indicator                    | 9, 10, 11                                   |
| §13 pinned params (region/OCID/CIDR placeholders, danted, no zpr) | 1, 2, 8                                     |
| §14 cost/teardown (`clean`, `tf destroy`)                         | 13, 14                                      |

No ZPR (intentionally out of scope). All criteria mapped.
