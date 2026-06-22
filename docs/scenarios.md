# Deployment Scenarios — Cloud vs On-Prem, Driver & DB Versions

This PoC connects an Oracle JDBC application to a private Autonomous Database (ADB) through a SOCKS5 proxy. It works against the specific environment it targets: a 23ai-line thin driver on JDK 21, talking to an ADB private endpoint through a Dante (`sockd`) relay or an OCI Bastion.

This document explains **what makes that work**, then walks each axis a real deployment might differ on — driver line, JDK, database version, network topology, and proxy type — and states **what the PoC would have to change** to follow. The short version up front: the database _version_ is the variable that matters least. The driver, and the network topology between the client and the listener, are what matter most.

---

## What actually makes this work

Three properties are load-bearing. Everything else is incidental.

1. **A modern thin driver using connection-scoped `oracle.net.socks*` properties over NIO.** The driver opens its TCP socket to the database through the SOCKS5 proxy because the connection carries `oracle.net.socksProxyHost`, `oracle.net.socksProxyPort`, and `oracle.net.socksRemoteDNS`. These are set per-connection on the pool data source (`DataSourceConfig.java`), not as JVM-wide system properties. This distinction is critical — see [Driver line](#driver-line-ojdbc8--ojdbc11--ojdbc17).

2. **A single database endpoint the proxy can reach.** The driver makes one TCP connection, to one address, and that address is reachable from the proxy host. ADB satisfies this by design: OCI presents the database as a single private endpoint and handles any internal routing itself, so the client never gets told to reconnect somewhere else. The moment the database tells the client to reconnect to a _different_ address (as Oracle RAC does — see [Topology](#topology-cloud-adb-vs-on-prem)), the model needs more than a plain relay.

3. **Remote DNS.** With `oracle.net.socksRemoteDNS=true`, the client sends the database's private FQDN to the proxy _unresolved_, and the proxy resolves it on its own side of the network — where that private name actually resolves. With it off, the client tries to resolve the private FQDN locally, fails, and never reaches the proxy.

If a target environment preserves these three properties, the PoC ports with minimal change. If it breaks one of them, that break is the work.

---

## Driver line (ojdbc8 / ojdbc11 / ojdbc17)

The driver — not the database — owns SOCKS behavior. The PoC uses:

```
com.oracle.database.jdbc:ojdbc17:23.8.0.25.04
com.oracle.database.jdbc:ucp17:23.8.0.25.04
com.oracle.database.security:oraclepki:23.8.0.25.04
```

### Driver artifact vs JDK

| Artifact  | JDK            | JDBC spec | API namespace           |
| --------- | -------------- | --------- | ----------------------- |
| `ojdbc8`  | 8, 11          | 4.2       | `javax.*`               |
| `ojdbc11` | 11+            | 4.3       | `javax.*`               |
| `ojdbc17` | 17, 19, 21, 25 | 4.3       | `jakarta.*` (from 23.6) |

The PoC runs Spring Boot 4.x, which is built on Jakarta EE, so it requires the `jakarta.*` driver — `ojdbc17`. An app still on `javax.*` (older Spring Boot, Java EE) would use `ojdbc8` or `ojdbc11` instead. The SOCKS properties are identical across all three.

### Version-number scheme and the "26ai" rename

Two schemes are in play:

- **19c / 21c**: `X.X.X.X` — e.g. `19.25.0.0`, `21.17.0.0`.
- **23ai / 26ai**: `X.X.X.X.YY.MM` — e.g. the PoC's `23.8.0.25.04` (the 8th release update, April 2025).

Oracle renamed **Database 23ai** to **Oracle AI Database 26ai** (GA on-prem January 2026, release update `23.26.1`). The second number is the release _year_, not a new major version — 26ai is the same product family, and the JDBC SOCKS behavior is unchanged. The "26ai driver" is simply the `23.26.x` line. A move from the PoC's `23.8` driver to a 26ai `23.26.x` driver is a version bump, not a redesign.

### The 12.2 NIO break — why the property _prefix_ matters

There are two different SOCKS mechanisms, and only one of them works on modern drivers:

- **Legacy JVM SOCKS** — the standard Java `socksProxyHost` / `java.net.socks.*` system properties. These routed the thin driver through a proxy in **11.x and 12.1.x**. Starting in **12.2**, when the thin driver moved to non-blocking NIO, these settings silently stopped taking effect — connections quietly bypassed the proxy (Oracle MOS note 2589708.1).
- **Native Oracle SOCKS** — the `oracle.net.socksProxyHost` / `oracle.net.socksProxyPort` / `oracle.net.socksRemoteDNS` connection properties. These are understood by the driver's own NIO network layer and are the supported path on current drivers. This is what the PoC uses.

The practical rule for any driver from 12.2 onward: use the `oracle.net.`-prefixed connection properties, never the JVM `java.net.socks.*` ones. The PoC's README captures the same point; it generalizes to every modern driver line.

### Driver ↔ database compatibility

A current driver talks to old databases. The 23ai/26ai driver line is certified against **19c, 21c, 23ai, and 26ai** databases. So "modern driver, old database" is fully supported and changes nothing about the SOCKS path:

| Database | Works with 23ai/26ai driver (`ojdbc17`)? |
| -------- | ---------------------------------------- |
| 26ai     | Yes                                      |
| 23ai     | Yes                                      |
| 21c      | Yes                                      |
| 19c      | Yes                                      |

The takeaway: keep the newest driver your JDK supports, regardless of the database's age.

---

## JDK

`ojdbc17` is certified on JDK 17, 19, 21, and 25. The PoC pins **JDK 21** — the current LTS and the safe default for this driver line. JDK 25/26 certification trails the LTS, so pin to 21 unless a newer LTS is explicitly certified for the driver you ship. To move down to JDK 11 or 8, switch the driver artifact (`ojdbc11` / `ojdbc8`) and the app's API namespace accordingly; the SOCKS configuration is unaffected.

---

## Database version (19c / 21c / 23ai / 26ai)

This is the axis that changes the least. The SOCKS path is a driver-and-network concern; the database does not participate in it. Across 19c → 26ai, the proxy setup is identical.

Two things that _do_ differ by version:

**Wallet jar set (mTLS).** This is tied to the _driver_ line, not the database:

| Driver line | Jars required for Oracle Wallet                     |
| ----------- | --------------------------------------------------- |
| 23ai / 26ai | `oraclepki.jar` only                                |
| 19c / 21c   | `oraclepki.jar` + `osdt_cert.jar` + `osdt_core.jar` |

The PoC ships only `oraclepki` because it is on the 23ai line. A deployment built against a 19c/21c driver must add the two `osdt_*` jars or wallet loading fails.

**TLS availability and ports.** ADB exposes TLS/mTLS on 1522. On-prem listeners may offer plain TCP (1521), TCPS (typically 2484), or Oracle native network encryption over TCP. The choice affects the connect descriptor and credential setup, not the proxy — see the next section.

---

## Topology: cloud (ADB) vs on-prem

This is where environments genuinely diverge, and where most of the porting effort lives.

### Cloud — ADB (what the PoC targets)

ADB is reachable as a **single private endpoint**. OCI fronts the database with its own connection routing, so the client makes exactly one connection, to one address, and is never told to reconnect elsewhere. That is precisely why a plain SOCKS5 relay is sufficient. Authentication is mTLS with a wallet (the PoC default) or walletless TLS using a full connect string from the ADB console.

### On-prem — single instance, single listener

A non-clustered database behind one listener behaves like ADB from the proxy's point of view: one endpoint, no redirect. Porting the PoC here is small:

- Point the connection at the on-prem listener instead of the ADB TNS alias.
- Supply the right credential model: a wallet/TCPS, or Oracle **native network encryption**. Native encryption is **transparent to SOCKS** — SOCKS wraps the raw TCP stream beneath SQL\*Net, so the encryption negotiation rides through the tunnel untouched. No proxy change is needed for it.
- Open the proxy's egress rule to the listener host and port (the PoC's `sockd` rule currently allows only the ADB FQDN on 1522).

### On-prem — Oracle RAC behind SCAN (the landmine)

This is the case that breaks the "single endpoint" invariant, so it gets the most attention.

**Why it breaks.** A client connects to the **SCAN** (Single Client Access Name) listener, which does _not_ proxy the session. Instead it **redirects** the client: it replies "reconnect to node VIP _X_," and the client opens a **second TCP connection** to that node's virtual IP. For SOCKS, this second connection must _also_ traverse the proxy and reach that VIP. Two things commonly go wrong:

1. The redirect hands back a **node VIP the proxy host cannot route to or resolve**, so the second connection times out.
2. The proxy's egress allow-list only covers the SCAN address, not the individual node VIPs.

The PoC as written would connect to the SCAN, then hang on the redirect.

**What the PoC must change** — three options, in order of robustness:

| Option                                                                   | What changes                                                                                                                                                                                                                                                                                                                                                                                                         | Trade-off                                                                                                                                                   |
| ------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A — Widen proxy reachability**                                         | Extend the `sockd` `socks pass` rule (and the NSG/firewall egress) to allow the SCAN name _and every node VIP_ on the listener port; ensure remote DNS resolves those VIP names on the proxy side; place the proxy host where it can route to the RAC interconnect/VIP subnet. Driver config is unchanged.                                                                                                           | Minimal new infrastructure, but fragile: VIPs and node membership change, and every node must stay individually reachable and resolvable through the proxy. |
| **B — Oracle Connection Manager (CMAN) in front of RAC** _(recommended)_ | Stand up a CMAN instance in the database network and point the PoC's SOCKS target at CMAN's single endpoint. CMAN is a protocol-aware Oracle Net proxy: it understands SCAN and **resolves the redirect server-side**, so the client sees one endpoint and never the node VIPs — exactly mirroring how ADB hides routing. The proxy needs egress only to CMAN; the driver's `oracle.net.socks*` config is unchanged. | Most robust and preserves RAC load-balancing and failover, plus adds source-IP/service access control. Costs a CMAN host to deploy and maintain.            |
| **C — Bypass SCAN (test only)**                                          | Connect directly to a single node's local listener (node VIP + service) instead of the SCAN. One endpoint, no redirect.                                                                                                                                                                                                                                                                                              | Quick to prove connectivity, but gives up RAC load-balancing and failover. Not for production.                                                              |

Option B is the on-prem analogue of what OCI already does for ADB, which is why it is the natural fit.

### Concrete configurations

**`sockd.conf` — on-prem single listener.** The PoC's relay template with the destination changed from the ADB FQDN on 1522 to an on-prem listener on 1521 (use 2484 for TCPS). Egress is pinned to the one listener endpoint, ingress to the client CIDR:

```
logoutput: /var/log/sockd.log
internal: 0.0.0.0 port = 1080
external: eth0

socksmethod: none
clientmethod: none
user.privileged: root
user.unprivileged: nobody

client pass {
    from: 203.0.113.0/24 to: 0.0.0.0/0
    log: connect disconnect error
}

socks pass {
    from: 203.0.113.0/24 to: db.corp.example.com port = 1521
    protocol: tcp
    log: connect disconnect error
}

socks block {
    from: 0.0.0.0/0 to: 0.0.0.0/0
    log: connect error
}
```

**`sockd.conf` — RAC, Option A (widen reachability).** The relay must allow the SCAN name _and every node VIP_ on the listener port, because the client follows the SCAN redirect to a VIP. Add one `socks pass` rule per node:

```
client pass {
    from: 203.0.113.0/24 to: 0.0.0.0/0
    log: connect disconnect error
}

# SCAN entry point
socks pass {
    from: 203.0.113.0/24 to: rac-scan.corp.example.com port = 1521
    protocol: tcp
    log: connect disconnect error
}

# One rule per node VIP the SCAN may redirect to
socks pass {
    from: 203.0.113.0/24 to: rac01-vip.corp.example.com port = 1521
    protocol: tcp
}
socks pass {
    from: 203.0.113.0/24 to: rac02-vip.corp.example.com port = 1521
    protocol: tcp
}

socks block {
    from: 0.0.0.0/0 to: 0.0.0.0/0
    log: connect error
}
```

With `oracle.net.socksRemoteDNS=true`, the VIP hostnames resolve on the proxy host, so the proxy must also resolve and route to them. Every node added to the cluster needs another rule — the maintenance cost the table above flags.

**CMAN — Option B (recommended).** Run Oracle Connection Manager (its traffic-director mode, CMAN-TDM, is the variant tightly integrated with SCAN) on a host inside the database network. `cman.ora`:

```
cman_proxy =
  (configuration =
    (address = (protocol = tcp)(host = cman.corp.example.com)(port = 1521))
    (rule_list =
      (rule =
        (source = 203.0.113.0/24)(destination = rac-scan.corp.example.com)(srv = SALESPDB)
        (action = accept))
      (rule =
        (source = *)(destination = *)(srv = *)(action = reject)))
    (parameter_list =
      (idle_timeout = 0)
      (inbound_connect_timeout = 60)
      (max_connections = 1024)
      (log_level = user)))
```

Start it with `cmctl -c cman_proxy startup`. CMAN sits in the database network, so it follows the SCAN redirect to the node VIPs itself; the client only ever talks to CMAN. The client reaches the cluster through a `SOURCE_ROUTE` descriptor — CMAN first, then the SCAN target behind it:

```
jdbc:oracle:thin:@(DESCRIPTION =
  (SOURCE_ROUTE = yes)
  (ADDRESS = (PROTOCOL = tcp)(HOST = cman.corp.example.com)(PORT = 1521))
  (ADDRESS = (PROTOCOL = tcp)(HOST = rac-scan.corp.example.com)(PORT = 1521))
  (CONNECT_DATA = (SERVICE_NAME = SALESPDB)))
```

The relay then needs egress only to the single CMAN endpoint — use the single-listener `sockd.conf` above with the destination set to `cman.corp.example.com port = 1521`. No per-VIP rules, and no relay change when the cluster grows.

---

## Proxy / auth variations

The PoC's relay requires no SOCKS-layer authentication; security comes from a source-IP allow-list plus end-to-end mTLS to the database. Two variations change that:

**The proxy requires SOCKS authentication.** The driver's NIO SOCKS client offers only the no-auth method (`0x00`) during the SOCKS handshake — it cannot negotiate RFC-1929 username/password auth (the PoC demonstrates this empirically in `DEMO.md` §5). If the target proxy _requires_ auth, two fallbacks exist:

- **SSH dynamic forwarding** — run `ssh -D` to a host that can reach the database, and point the driver at the local `127.0.0.1` listener. Authentication happens at the SSH layer; the SOCKS side stays no-auth. This is the OCI Bastion mode the PoC already supports.
- **Blocking-IO legacy path** — `oracle.jdbc.javaNetNio=false` plus the JVM `java.net.socks.*` properties. This re-enables the pre-NIO SOCKS client, which _can_ do user/pass auth, at the cost of disabling non-blocking I/O driver-wide (a performance penalty). Last resort only.

**A local relay instead of a remote daemon.** The OCI Bastion mode replaces the always-on `sockd` with an ephemeral dynamic port-forwarding session on `127.0.0.1`. Only `SOCKS_HOST`/`SOCKS_PORT` change; the driver behavior is identical. Bastion sessions have a hard 3-hour TTL, so they suit demos and ad-hoc access rather than always-on use.

---

## Decision matrix — what to change for a given target

| Target axis                             | Setting                                                                  | Where                                        |
| --------------------------------------- | ------------------------------------------------------------------------ | -------------------------------------------- |
| App on `javax.*` (older Spring/Java EE) | Use `ojdbc8`/`ucp8` or `ojdbc11`/`ucp11`                                 | `app/build.gradle`                           |
| JDK 8 / 11                              | Match driver artifact to the JDK                                         | `app/build.gradle` toolchain + dependencies  |
| Driver against 19c/21c                  | Add `osdt_cert.jar` + `osdt_core.jar` for wallets                        | `app/build.gradle`                           |
| Database 19c → 26ai                     | No SOCKS change                                                          | —                                            |
| On-prem single listener                 | Repoint URL; open proxy egress to listener host:port                     | connection URL, `sockd.conf` / NSG           |
| On-prem native encryption               | No proxy change (transparent through SOCKS)                              | —                                            |
| On-prem RAC / SCAN                      | Front with CMAN (preferred) or widen proxy reachability to all node VIPs | new CMAN host _or_ `sockd.conf` + NSG egress |
| Proxy requires auth                     | SSH `-D` local relay, or `javaNetNio=false` + `java.net.socks.*`         | runtime/JVM args                             |

---

## Load-bearing properties — quick reference

| Property                    | Purpose                                        | Notes                                                        |
| --------------------------- | ---------------------------------------------- | ------------------------------------------------------------ |
| `oracle.net.socksProxyHost` | Proxy host the driver tunnels through          | Connection-scoped, not JVM-wide                              |
| `oracle.net.socksProxyPort` | Proxy port (1080 here)                         | —                                                            |
| `oracle.net.socksRemoteDNS` | Resolve the DB FQDN on the proxy side          | Must be `true` for a private endpoint                        |
| `oracle.jdbc.javaNetNio`    | Toggles NIO (`true`/default) vs blocking SOCKS | Leave default; set `false` only for the legacy auth fallback |

---

## References

- [Oracle JDBC and UCP Downloads](https://www.oracle.com/database/technologies/appdev/jdbc-downloads.html) — driver/JDK certification matrix
- [Oracle JDBC FAQ](https://www.oracle.com/database/technologies/faq-jdbc.html) — artifact and wallet-jar requirements
- [MOS 2589708.1 — SOCKS Proxy No Longer Working With Oracle JDBC Thin Driver](https://support.oracle.com/knowledge/Middleware/2589708_1.html) — the 12.2 NIO break
- [Configuring Oracle Connection Manager (19c)](https://docs.oracle.com/en/database/oracle/oracle-database/19/netag/configuring-oracle-connection-manager.html) — CMAN as a RAC-aware proxy
- [Connecting to Oracle Database from outside OCI](https://blogs.oracle.com/cloud-infrastructure/connect-to-oracle-db-from-outside-oci) — single-endpoint patterns for RAC/ADB
- [Oracle AI Database 26ai GA announcement](https://blogs.oracle.com/database/ga-of-oracle-ai-database-26ai-for-linux-x86-64-on-premises-platforms) — the 23ai → 26ai rename and version scheme
