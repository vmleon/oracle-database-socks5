# DEMO — Oracle JDBC over SOCKS5 Validation

This document is the demo script. There is no `manage.py demo` command; execute these steps in order.

Prerequisites: infrastructure provisioned (`python manage.py tf apply`), danted installed and running (`python manage.py provision`), wallet fetched (`python manage.py wallet fetch`), app built (`python manage.py build`), `.env` populated.

---

## Step 1 — Independent SOCKS test (no Java)

Verify the tunnel before touching the JVM. Run both variants from the client machine.

**Remote-DNS path (must succeed):**

```bash
curl -v --socks5-hostname JUMPHOST_IP:1080 telnet://<adb-private-fqdn>:1522
```

Expected: `curl` connects through the proxy, the proxy resolves `<adb-private-fqdn>` inside the VCN and opens TCP 1522. You see a TLS handshake banner or an immediate close (the server expects a TLS client hello, not a raw telnet opener) — either way the TCP connection was established. `curl` reports `Connected to <adb-private-fqdn>`.

**Local-DNS path (must fail):**

```bash
curl -v --socks5 JUMPHOST_IP:1080 telnet://<adb-private-fqdn>:1522
```

Expected: `curl` attempts to resolve `<adb-private-fqdn>` on the client machine before forwarding. The client has no route to the ADB private DNS zone → `Could not resolve host` or similar. This confirms that `oracle.net.socksRemoteDNS=true` is not optional.

---

## Step 2 — SQLcl through the proxy (optional)

Requires SQLcl 24.x+. Useful as a quick wallet validation before starting the app.

```sql
SET socksproxy socks5h://JUMPHOST_IP:1080
SET cloudconfig wallet/wallet.zip
CONNECT DB_USER/DB_PASSWORD@dbpoc_high
SELECT 1 FROM DUAL;
```

Expected: `1` returned. If this fails after step 1 succeeded, check the wallet path and TNS alias.

---

## Step 3 — App happy path

Start the app (reads `SOCKS_HOST`, `SOCKS_PORT`, `SOCKS_REMOTE_DNS`, `TNS_ALIAS`, `WALLET_PATH`, `DB_USER`, `DB_PASSWORD` from `.env` / environment):

```bash
python manage.py run
```

In a second terminal, probe the health endpoint:

```bash
python manage.py health
```

Expected response (`status` == `UP`, exit 0):

```json
{
  "status": "UP",
  "components": {
    "db": {
      "status": "UP",
      "details": {
        "latencyMs": 42,
        "borrowed": 1,
        "available": 9,
        "socks": "JUMPHOST_IP:1080",
        "mode": "jumphost"
      }
    },
    "readiness": { "status": "UP" },
    "liveness": { "status": "UP" }
  }
}
```

Values for `latencyMs`, `borrowed`, and `available` vary; `socks` and `mode` reflect the running configuration.

---

## Step 4 — Negative test: remote DNS off

Stop the running app (`Ctrl-C`), override `SOCKS_REMOTE_DNS`, and restart:

```bash
SOCKS_REMOTE_DNS=false python manage.py run
```

Then probe:

```bash
python manage.py health
```

Expected response (`status` == `DOWN`, exit 1):

```json
{
  "status": "DOWN",
  "components": {
    "db": {
      "status": "DOWN",
      "details": {
        "error": "SQLException: IO Error",
        "socks": "JUMPHOST_IP:1080",
        "mode": "jumphost"
      }
    },
    "readiness": { "status": "DOWN" }
  }
}
```

The client tries to resolve `<adb-private-fqdn>` locally, fails (no route to the VCN private DNS zone), and the JDBC connection never reaches the proxy. This is the single most common misconfiguration.

Restore and confirm recovery:

```bash
# Ctrl-C the running process, then:
python manage.py run        # SOCKS_REMOTE_DNS defaults to true via application.yml
python manage.py health     # expect UP
```

---

## Step 5 — SOCKS authentication experiment (§6.1)

This section empirically determines whether the Oracle JDBC NIO SOCKS client can negotiate RFC-1929 user/pass authentication. The expected outcome per the mechanism is rejection (the NIO client offers only method `0x00`), but the wire evidence must confirm it.

### 5a — Enable danted authentication

On the **Ansible control node**, edit `ansible/roles/socks5/defaults/main.yml` or pass extra vars:

```bash
python manage.py provision \
  -e socks_auth_method=username \
  -e socks_debug=2 \
  -e socks_username=socksuser \
  -e "socks_password=<choose-a-test-password>"
```

Alternatively, set the vars directly and re-run provision:

```yaml
# ansible/roles/socks5/defaults/main.yml (temporary — revert after experiment)
socks_auth_method: username
socks_debug: 2
socks_username: socksuser
socks_password: "<test-password>"
```

```bash
python manage.py provision
```

danted runs with `method: username`, `debug: 2`, and writes method-negotiation lines to `/var/log/danted.log`.

### 5b — Capture wire evidence on the jump host

Open a capture session on the jump host **before** running the app:

```bash
# Option A: tcpdump (raw bytes)
sudo tcpdump -i any -X port 1080

# Option B: danted debug log (requires socks_debug=2)
sudo tail -f /var/log/danted.log
```

### 5c — Attempt the "brave" path (A): native SOCKS props + NIO on + credentials

Stop the app if running. Supply credential system properties alongside the native oracle.net SOCKS properties:

```bash
SOCKS_HOST=JUMPHOST_IP SOCKS_PORT=1080 SOCKS_REMOTE_DNS=true \
java \
  -Djava.net.socks.username=socksuser \
  -Djava.net.socks.password=<test-password> \
  -Doracle.net.socksProxyUsername=socksuser \
  -Doracle.net.socksProxyPassword=<test-password> \
  -jar app/build/libs/socks5poc-*.jar
```

(The `oracle.net.socksProxyUsername/Password` names are candidate undocumented properties on the 26ai driver — include them to catch any such knob.)

Observe the tcpdump or danted log. The **first client→proxy bytes** of the SOCKS5 greeting are the evidence:

| Greeting bytes (hex) | Interpretation                                                                                                                                            |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `05 01 00`           | Client offered **one method: no-auth only**. Proxy requires auth → rejects with `05 FF`. NIO SOCKS auth is not possible.                                  |
| `05 02 00 02`        | Client offered **two methods: no-auth and user/pass**. If proxy accepts `02`, authentication proceeds → connection succeeds. NIO SOCKS auth is supported. |

Then probe health:

```bash
python manage.py health
```

**Expected:** health returns `DOWN`. danted logs `method username: no acceptable authentication method`. tcpdump shows `05 01 00` — the driver offered only no-auth. The `java.net.socks.*` system properties are ignored by the NIO code path.

If health returns `UP` unexpectedly, the 26ai driver added a native SOCKS-auth property — record which property name produced the connection.

### 5d — Results

Record the empirical finding here after running the experiment:

| Offered methods (hex)    | danted response      | Health outcome | App mode    |
| ------------------------ | -------------------- | -------------- | ----------- |
| _(fill in from tcpdump)_ | _(fill in from log)_ | _(UP / DOWN)_  | _(fill in)_ |

**Expected result:** `05 01 00` / `FF` / `DOWN` / mode (B) — confirming the NIO client cannot do SOCKS-layer auth.

### 5e — Revert danted to no-auth and confirm mode (B)

Restore the production configuration:

```bash
python manage.py provision \
  -e socks_auth_method=none \
  -e socks_debug=0
```

Or restore `ansible/roles/socks5/defaults/main.yml`:

```yaml
socks_auth_method: none
socks_debug: 0
```

```bash
python manage.py provision
```

Restart the app (no credential properties required in mode B):

```bash
python manage.py run
python manage.py health   # expect UP
```

Mode (B): `oracle.net.socks*` properties only, NIO on, no SOCKS-layer auth. Security is the NSG source-IP allowlist (ingress 1080 from `CLIENT_CIDR` only) plus end-to-end mTLS between the client and ADB — the relay never sees plaintext and holds no DB credentials.

---

## Step 6 — Mode swap: Bastion dynamic port-forwarding

The Bastion mode uses an OCI Bastion dynamic port-forwarding session as the SOCKS5 endpoint instead of the always-on danted daemon. The JDBC driver behavior is identical; only `SOCKS_HOST` and `SOCKS_PORT` change.

Start an OCI Bastion session (outside manage.py — use the OCI console or CLI):

```bash
# Create a dynamic port-forwarding session via OCI CLI (example):
oci bastion session create-dynamic-port-forwarding-session \
  --bastion-id <BASTION_OCID> \
  --display-name socks5-demo \
  --session-ttl-in-seconds 10800 \
  --key-details '{"publicKeyContent":"<ssh-pub-key>"}'

# Once ACTIVE, open the local tunnel:
ssh -N -D 127.0.0.1:1080 \
  -o StrictHostKeyChecking=no \
  -i ~/.ssh/bastion_key \
  -p 22 <session-id>@host.bastion.<region>.oci.oraclecloud.com &
```

Then run the app pointing at the local listener:

```bash
MODE=bastion SOCKS_HOST=127.0.0.1 SOCKS_PORT=1080 python manage.py run
python manage.py health
```

Expected: identical `UP` response with `"mode": "bastion"` and `"socks": "127.0.0.1:1080"`.

**Bastion TTL:** sessions have a hard ceiling of 3 hours (maximum `session-ttl-in-seconds 10800`). This is an OCI platform limit that cannot be raised. Use jump-host mode for any always-on requirement.

---

## Gotcha: tnsping does not traverse SOCKS

`tnsping` and the Oracle thick/OCI client use a different network layer that does not respect the `oracle.net.socks*` JDBC properties. A failing `tnsping <adb-private-fqdn>` alongside a working JDBC connection (health `UP`) is the correct and expected outcome. Use `curl --socks5-hostname` (step 1) or the app health endpoint as the connectivity oracle, not `tnsping`.

---

## Summary

| Step                  | Command                                                        | Expected                        |
| --------------------- | -------------------------------------------------------------- | ------------------------------- |
| 1a SOCKS (remote DNS) | `curl --socks5-hostname JUMPHOST_IP:1080 telnet://<fqdn>:1522` | TCP connected                   |
| 1b SOCKS (local DNS)  | `curl --socks5 JUMPHOST_IP:1080 telnet://<fqdn>:1522`          | Host resolution failure         |
| 2 SQLcl               | `CONNECT user/pwd@dbpoc_high`                                  | `1` returned                    |
| 3 Happy path          | `manage.py run` + `manage.py health`                           | `UP` with details               |
| 4 Negative test       | `SOCKS_REMOTE_DNS=false manage.py run` + health                | `DOWN`                          |
| 5a–d Auth experiment  | provision + tcpdump + brave path                               | `05 01 00` / rejection / `DOWN` |
| 5e Revert             | provision (`socks_auth_method=none`) + health                  | `UP` mode (B)                   |
| 6 Bastion             | `MODE=bastion SOCKS_HOST=127.0.0.1 manage.py run` + health     | `UP` `"mode":"bastion"`         |
