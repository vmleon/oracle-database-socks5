# Oracle JDBC over SOCKS5 — PoC

Proves that a Java client (Spring Boot + UCP + ojdbc17) can reach a **private Oracle Autonomous Database (ADB-S, mTLS)** through a **SOCKS5 proxy**, with the proxy acting as a dumb relay that never holds a wallet or decrypts traffic.

**End-to-end path (primary):**

```
Java client (UCP/ojdbc17, holds wallet) ──SOCKS5──▶ jump host danted:1080 (public subnet) ──▶ private ADB-S:1522 (mTLS end-to-end)
```

The proxy is a transparent TCP relay. mTLS is between the Java client and ADB — the jump host sees only ciphertext and the destination address.

---

## Topology

```mermaid
flowchart LR
  subgraph Client["Client (outside OCI)"]
    APP["Spring Boot + UCP + ojdbc17\nsocksProxyHost=JUMPHOST_IP\nsocksProxyPort=1080\nsocksRemoteDNS=true\nholds ADB wallet (mTLS)"]
  end
  subgraph OCI["OCI VCN"]
    subgraph PUB["Public subnet"]
      JH["Jump host (compute)\ndanted SOCKS5 :1080\nNSG: ingress 1080 from CLIENT_CIDR only\ndumb relay, no wallet"]
    end
    subgraph PRIV["Private subnet"]
      PE["ADB-S private endpoint :1522 (mTLS)"]
    end
    JH -->|"resolve FQDN + TCP 1522"| PE
  end
  APP ==>|"SOCKS5 — end-to-end mTLS preserved"| JH
```

---

## Decisions

| Area                               | Choice                                                                     | Why / when to use something else                                                                                                                                                                                                            |
| ---------------------------------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **DB**                             | ADB-S 26ai, private endpoint, mTLS wallet                                  | Exercises wallet + private DNS + mTLS end-to-end. Toggle `auth_mode=tls` for the walletless path (§ Auth mode).                                                                                                                             |
| **Connectivity (primary)**         | Self-managed jump host running danted (SOCKS5 daemon)                      | Always-on; no TTL; faithfully models a SOCKS5 proxy owned by the DB team.                                                                                                                                                                   |
| **Connectivity (demo)**            | OCI Bastion dynamic port-forwarding (SOCKS5)                               | Zero infra, free, IAM-controlled. 3-hour hard TTL — demo and ad-hoc access only.                                                                                                                                                            |
| **Connectivity (alt, documented)** | CMAN-TDM                                                                   | Oracle-native chokepoint with per-service rules. Use when the requirement is "controlled chokepoint, no VPN" rather than literal SOCKS5. CMAN terminates Oracle Net and holds a wallet — does not satisfy "proxy must not decrypt traffic." |
| **DNS**                            | `oracle.net.socksRemoteDNS=true`                                           | ADB private FQDN resolves only inside the VCN; the jump host does the lookup. `false` → `host not found`.                                                                                                                                   |
| **JDBC stack**                     | ojdbc17 / ucp17 / oraclepki **23.8.0.25.04**, JDK 21 LTS                   | Native `oracle.net.socks*` properties are connection-scoped, not JVM-wide. JDK 21 is the certified sweet spot for this driver version.                                                                                                      |
| **SOCKS auth**                     | No SOCKS-layer auth (mode B); NSG source-IP allowlist + end-to-end mTLS    | The native NIO SOCKS client advertises only no-auth (`0x00`). Security is enforced at the DB and TLS layers, not at the relay.                                                                                                              |
| **Proxy trust model**              | Dumb relay — jump host holds no wallet, sees only ciphertext + destination | Minimizes what the chokepoint can access; mTLS stays client↔ADB.                                                                                                                                                                            |
| **App**                            | Spring Boot 4.1, Java 21, virtual threads on                               | Current GA (Spring Framework 7 / Jakarta EE 11 / Tomcat 11). UCP integrates wallet + connection validation.                                                                                                                                 |
| **Network security**               | NSG source-IP allowlist                                                    | ZPR (Zero Trust Packet Routing) is intentionally out of scope for this PoC; NSG source-IP is the network control.                                                                                                                           |
| **Config mgmt**                    | Ansible role for the jump host                                             | Repeatable SOCKS daemon install + hardening against a real compute host.                                                                                                                                                                    |

---

## Connectivity comparison

|                                        | Jump host SOCKS5            | OCI Bastion SOCKS5   | CMAN-TDM                          |
| -------------------------------------- | --------------------------- | -------------------- | --------------------------------- |
| Always-on                              | **Yes**                     | No (≤ 3 h TTL)       | Yes                               |
| You patch/own the host                 | Yes                         | No (managed)         | Yes                               |
| Proxy sees plaintext / holds DB creds  | **No** (dumb relay)         | No (dumb relay)      | **Yes** (terminates + wallet)     |
| Satisfies literal "SOCKS5" requirement | **Yes**                     | Yes                  | **No**                            |
| Rule granularity                       | NSG + IP                    | IAM + CIDR           | Per-service Oracle Net rules      |
| Best for                               | Production always-on SOCKS5 | Demo / ad-hoc access | "Chokepoint, not VPN" requirement |

---

## Quickstart

Full step-by-step instructions are in **[DEPLOY.md](docs/DEPLOY.md)**. The demo narrative is in **[DEMO.md](docs/DEMO.md)**.

### Command flow

```bash
# 1. Check prerequisites and seed .env
python manage.py setup

# 2. Provision infra (VCN + ADB-S + jump host)
python manage.py tf apply

# 3. Install and harden danted on the jump host
python manage.py provision

# 4. Download a fresh wallet into wallet/
python manage.py wallet fetch

# 5. Build the Spring Boot jar
python manage.py build
# Runs: ./gradlew bootJar   Output: app/build/libs/socks5poc-*.jar

# 6. Start the app
python manage.py run

# 7. Confirm DB connectivity through the proxy
python manage.py health
# Expects: {"status":"UP"} with DB sub-check, latency, pool stats, socks host:port
```

Copy `.env.example` to `.env` and fill in your OCID, region, client CIDR, and DB password before running `setup`.

---

## Auth mode

Set `AUTH_MODE` in `.env` (or pass `--auth-mode` at runtime):

| `AUTH_MODE`      | What changes                                                                                                                                          |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `mtls` (default) | Wallet required. Set `WALLET_PATH`. `TNS_ADMIN` points at the wallet directory. Port 1522.                                                            |
| `tls`            | Walletless. No wallet download, no `TNS_ADMIN`, no cert-expiry risk. Use the TLS connect string from the ADB console. SOCKS properties are unchanged. |

The SOCKS path (`oracle.net.socksProxyHost/Port/RemoteDNS`) is identical for both modes; only wallet handling differs. `tls` is Oracle's recommended mode for private-endpoint / ACL-restricted ADB in production.

---

## Anti-patterns

**Legacy JVM SOCKS (`socksProxyHost` + `javaNetNio=false`)**
Using JVM system properties `java.net.socks.*` with `oracle.jdbc.javaNetNio=false` disables non-blocking I/O driver-wide. This is a global, performance-degrading setting that broke in driver versions 12.2–18c. Use the connection-scoped `oracle.net.socks*` properties instead.

**Missing `oracle.net.socksRemoteDNS=true`**
The ADB private FQDN is not resolvable outside the VCN. Without this property, the client attempts to resolve the FQDN locally, gets `host not found`, and the connection fails. The demo includes a negative test that reproduces this failure.

**Treating OCI Bastion as always-on**
OCI Bastion sessions have a hard 3-hour TTL (maximum, not raisable). Any reconnect loop that re-creates sessions before expiry has a gap window and is not suitable for a production data plane.

**Reusing a pre-2026 wallet (DigiCert G1)**
Wallets generated before 28 Jan 2026 carry DigiCert G1 roots, which stop working after 15 Apr 2026. Current wallets use G2. `manage.py wallet fetch` always pulls a fresh wallet — never reuse stale wallet material.

---

## Cost & teardown

- **ADB-S:** smallest ECPU count, autoscale off. Stop between sessions; `tf destroy` removes it completely.
- **Jump host:** smallest Flex or A1 shape. Stoppable when not in use.
- **OCI Bastion:** free; sessions are ephemeral.

```bash
python manage.py clean          # stop app, clear wallet/
python manage.py tf destroy     # tear down all OCI resources
```
