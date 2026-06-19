# Backlog

Improvements and follow-ups for the Oracle JDBC-over-SOCKS5 PoC, ordered by relevance to the PoC. The repository ships a complete, reviewed scaffold (Terraform, Ansible, Spring Boot app, `manage.py`, docs); the items below are what turns the scaffold into a demonstrated, hardened, and pitchable result.

---

## P1 — Run the live deployment and record results

The acceptance criteria are runtime demonstrations against a real OCI tenancy. The code and `DEMO.md` steps exist, but no live run has been executed.

- Provision against a real tenancy: fill `.env` + `infra/terraform/terraform.tfvars`, then `tf apply` → `provision` → `wallet fetch` → `build` → `run` → `health`.
- Confirm `GET /actuator/health` returns `UP` with the DB sub-check executing `SELECT 1 FROM DUAL` through the proxy.
- Confirm the remote-DNS negative test: `SOCKS_REMOTE_DNS=false` yields readiness `DOWN` with a name-resolution/IO error; re-enabling returns `UP`.
- **Record the §6.1 SOCKS-auth experiment result.** `DEMO.md` step 5 contains the harness and an empty results table. Run it on the live jump host (`socks_auth_method=username`, `socks_debug=2`), capture the greeting bytes via `tcpdump`/danted debug, and fill in the table (offered methods, danted response, health outcome, shipped mode). Expected: `05 01 00` / `FF` / `DOWN` → confirms the NIO client offers only no-auth and the app ships in mode B. Confirm on the wire rather than asserting.

## P2 — One-command Bastion demo path

The Bastion mode swap is currently manual (`DEMO.md` documents a hand-run `ssh -N -D 127.0.0.1:1080` against an OCI Bastion dynamic-port-forwarding session).

- Implement `manage.py socks up --mode bastion`: generate an ephemeral key, create the dynamic port-forwarding session, poll for `ACTIVE`, launch the `ssh -D` tunnel in the background.
- Implement `manage.py socks down`: kill the ssh process and delete the session.
- Implement `manage.py demo`: script the `DEMO.md` §11 sequence end-to-end (happy path, negative test, mode swap), pausing where the §6.1 experiment needs the manual danted reconfigure.

## P3 — Security hardening for a credible demo

- **Gate health detail exposure.** The health endpoint reports `socks host:port` and `mode` with `management.endpoint.health.show-details: always`. For anything beyond a local demo, switch to `when-authorized` with an `ACTUATOR` role so infrastructure details aren't exposed on an unauthenticated endpoint.
- **Guard `SOCKS_HOST` at startup.** `DataSourceConfig` sets `oracle.net.socksProxyHost` from config; if `SOCKS_HOST` is unset it throws an opaque NPE at context startup. Fail fast with a clear message.
- **URL-encode the wallet path** in the mtls JDBC URL so a path with spaces or special characters does not produce a malformed URL.

## P4 — Exercise the walletless TLS auth mode

`auth_mode=tls` is wired (app branch + `DB_TLS_URL` in `.env.example`) but only the mtls path has been exercised.

- Set `is_mtls_connection_required = false` on the ADB (walletless TLS is available because the endpoint is private), populate `DB_TLS_URL` with the console TLS connect string, and demonstrate the simplification: no `TNS_ADMIN`, no wallet download, no cert-expiry. The SOCKS path and `oracle.net.socks*` properties are unchanged.

## P5 — Verify the Spring Boot 4.1 readiness group at runtime

`application.yml` defines `management.endpoint.health.group.readiness.include: db`. Spring Boot 4.1 reworked the health group/registry model; confirm on a running stack that `/actuator/health/readiness` actually reflects the DB indicator (and `/actuator/health/liveness` stays process-only).

## P6 — CMAN-TDM alternative (pitch + reference build)

Oracle Connection Manager in Traffic Director Mode is the alternative when the requirement is a _controlled chokepoint with no flat VPN_ rather than _literal SOCKS5_. It is an Oracle Net (L7) gateway, not a SOCKS proxy: the client uses a normal connect descriptor pointing at CMAN (no `socks*` properties), and CMAN terminates the Oracle Net session and re-originates it to the backend.

- **Trade-off to make explicit in any pitch:** CMAN terminates Oracle Net and authenticates to the DB with its own wallet — it sees session-level info and holds DB credentials. It is disqualified where the posture is "the proxy must not decrypt our traffic or authenticate to our database." A SOCKS5 relay only ever sees ciphertext + destination.
- **Strengths over SOCKS5:** per-service Oracle Net rule ACLs (lock to one service on one DB), session multiplexing, connection pooling (PRCP per-service/per-PDB).
- **Config shape (reference):** install on a VCN compute host that can reach the private DB; `cman.ora` with a listening endpoint, a `RULE_LIST` allowing only the client source → ADB service, a TLS endpoint, and a `wallet_location` for proxy auth to ADB; clients connect to CMAN's address/service. CMAN-TDM is licensed/feature-gated — confirm entitlement before proposing.

## P7 — ZPR (Zero Trust Packet Routing) hardening layer

ZPR enforces intent-based allow-only paths at the OCI fabric (client→jumphost:1080, jumphost→ADB:1522), blocking lateral movement even if NSGs are misconfigured — the "no lateral movement" answer. ZPR attributes attach to bastions and ADB.

- Add an optional `modules/zpr` (namespace, security attributes, default-deny policy) gated by an `enable_zpr` variable, defaulting off. NSG source-IP allowlist remains the baseline; ZPR layers on top.
- Requires `zpr-policy` / security-attribute admin policies. Note ZPR policy ordering in the deploy troubleshooting when enabled.

## P8 — Lean daemon and driver-version notes

- **`microsocks` as a lean alternative** to danted (~4 MB, `-w` IP-whitelist = mode B with no SOCKS-layer auth). Useful for a minimal jump host; danted is kept for full destination/port ACLs and the debug logging the §6.1 experiment relies on.
- **JDBC driver version:** the stack pins `ojdbc17`/`ucp17`/`oraclepki` `23.8.0.25.04` (the latest published line on Maven Central) on JDK 21. Revisit the pin as Oracle publishes newer 23.x patches; stay on JDK 21 LTS (JDK 25 has known ojdbc bugs, JDK 26 is uncertified).

## P9 — Discovery-call checklist

Questions that decide the architecture before building for a customer:

- Is the requirement **literal SOCKS5** or **"controlled chokepoint, no VPN"**? This decides SOCKS5 (this PoC) vs CMAN-TDM (P6).
- Does the customer's SOCKS proxy **require RFC-1929 auth**? This is the §6.1 question — if a proxy they don't control mandates SOCKS-layer auth and can't be fronted by SSH or a local relay, the last-resort path is `oracle.jdbc.javaNetNio=false` + JVM `java.net.socks.*` (blocking IO, performance cost).
- `auth_mode`: `mtls` (hardest path, wallet) vs `tls`/walletless (Oracle-recommended for a private-endpoint/ACL'd ADB). Always fetch a fresh G2 wallet for mtls.
