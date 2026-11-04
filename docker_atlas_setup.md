# Runbook: Apache Atlas API Testing Lab in Docker

A complete, self-contained guide to deploy an **Apache Atlas 2.1.0** lab inside Docker and exercise its REST APIs. Follow it top-to-bottom on any fresh Linux VM and you will end up with:

* A running Atlas server (Web UI + REST API) on port `21000`
* A custom metadata type (`my_custom_table`) registered via the Types API
* A metadata entity created, updated, and searchable through the v2 API
* Clean start/stop/restart behavior with persistent data and search index

> **Provenance:** this runbook was executed end-to-end on Ubuntu 24.04 (Docker 29.x). Several defects in the original version were found and fixed during execution — they are integrated into the steps below and documented in **Appendix A**. All files referenced here already exist in this folder; the inline contents allow rebuilding everything from this document alone.

---

## 1. Architecture Overview

The lab runs everything inside a **single container** from the community image `sburn/apache-atlas:2.1.0`:

```
┌─────────────────────── apache-atlas-lab container ───────────────────────┐
│  Apache Atlas 2.1.0 (Jetty, port 21000)                                  │
│    ├── embedded HBase        → graph/metadata store   (volume: atlas_data)│
│    ├── embedded ZooKeeper    → HBase + Kafka coordination (volume: atlas_data)│
│    ├── embedded Kafka        → Atlas notification bus (volume: atlas_data)│
│    └── embedded Solr         → search/indexing        (volume: atlas_solr)│
└──────────────────────────────────────────────────────────────────────────┘
          :21000 → host        admin / admin
```

**What persists across restarts** (named Docker volumes): HBase graph data, ZooKeeper/Kafka state, Solr Lucene indexes, and logs. `docker compose down -v` wipes all of it.

**Resource needs:** ≥ 2 vCPUs, 4–8 GB RAM, ~5 GB free disk (image is ~2.7 GB). First boot takes 2–5 minutes (about 1 minute on a fast VM); subsequent boots are faster.

---

## 2. Prerequisites

| Requirement | Check command | Notes |
|---|---|---|
| Linux VM (Ubuntu 20.04/22.04/24.04 or Debian) | `cat /etc/os-release` | 2 vCPU / 4–8 GB RAM |
| Docker Engine | `docker --version` | Install in Part A if missing |
| Docker Compose v2 plugin | `docker compose version` | Install in Part A if missing |
| `curl` | `curl --version` | For the API walkthrough |
| `python3` | `python3 --version` | Used by the smoke test |
| Outbound port `21000` open | firewall / security group | Only if accessing off-VM |

---

## Part A — Install Docker (skip if already installed)

```bash
# Ubuntu / Debian
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2
sudo systemctl enable --now docker

# Allow your user to run docker without sudo (log out/in afterwards)
sudo usermod -aG docker "$USER"
```

Verify:
```bash
docker --version          # e.g. Docker version 29.x
docker compose version    # e.g. Docker Compose version v2.x
docker run --rm hello-world   # pulls and runs a test container
```

> If `hello-world` works, Docker is ready. On RHEL/Fedora use `dnf install docker docker-compose-plugin` instead.

---

## Part B — Create the Project Files

All files live in **this folder** (`atlas_api_demo/`). If you are rebuilding from scratch, create the folder and each file exactly as given below.

```
atlas_api_demo/
├── docker_atlas_setup.md   ← this runbook
├── docker-compose.yml      ← deployment manifest (B.2)
├── atlas-entrypoint.sh     ← graceful start/stop wrapper (B.1)
├── typedef.json            ← custom type definition payload (B.3)
├── entity.json             ← entity creation payload (B.4)
├── entity-update.json      ← entity update payload (B.5)
└── smoke_test.sh           ← automated end-to-end verification (Part E)
```

### B.1 `atlas-entrypoint.sh` — graceful shutdown wrapper

**Why this file exists:** the stock image entrypoint (`bash -c 'atlas_start.py; tail -fF ...'`) never forwards `SIGTERM`. On `docker stop` (or a daemon/VM restart) the embedded HBase/ZooKeeper/Kafka processes are SIGKILLed; the persisted ZooKeeper state then contains a stale Kafka ephemeral node (`/brokers/ids/1`) whose owner session no longer matches, and **Atlas crash-loops forever** on the next start with:

```
Error while creating ephemeral at /brokers/ids/1, node already exists and owner
'117555349388918784' does not match current session '...' — Fatal error during KafkaServer startup
```

This wrapper traps `SIGTERM`/`SIGINT` and runs the image's own `atlas_stop.py`, which shuts down Atlas → Solr → HBase → ZooKeeper cleanly (closing ZK sessions removes stale ephemeral nodes). It also runs the starter with `python -u` so the startup marker is visible in `docker logs` (Python block-buffers stdout on pipes, which otherwise hides it), and exits the container if Atlas dies so `restart: unless-stopped` can self-heal.

```bash
#!/bin/bash
# Graceful start/stop wrapper for the Apache Atlas lab container.
ATLAS_HOME=/opt/apache-atlas-2.1.0
ATLAS_LOG="${ATLAS_HOME}/logs/application.log"

stop_handler() {
  echo "[atlas-lab-entrypoint] SIGTERM/SIGINT received - stopping Atlas stack gracefully..."
  python "${ATLAS_HOME}/bin/atlas_stop.py" || true
  echo "[atlas-lab-entrypoint] Atlas stack stopped."
  exit 0
}

trap stop_handler TERM INT

echo "[atlas-lab-entrypoint] starting Atlas stack..."
# -u: python block-buffers stdout on pipes, which would hide the
# "Apache Atlas Server process started!" marker from 'docker logs'
python -u "${ATLAS_HOME}/bin/atlas_start.py" &
STARTER_PID=$!

mkdir -p "$(dirname "$ATLAS_LOG")"
touch "$ATLAS_LOG"
tail -F "$ATLAS_LOG" &
TAIL_PID=$!

# Exit (so the restart policy can re-trigger) as soon as the starter
# exits, i.e. Atlas died, instead of tailing logs forever like the
# stock entrypoint does.
wait -n
EXIT_CODE=$?
kill "$TAIL_PID" 2>/dev/null
exit $EXIT_CODE
```

Make it executable:
```bash
chmod +x atlas-entrypoint.sh
```

### B.2 `docker-compose.yml` — deployment manifest

```yaml
services:
  apache-atlas:
    image: sburn/apache-atlas:2.1.0
    container_name: apache-atlas-lab
    environment:
      MANAGE_LOCAL_HBASE: "true"
      MANAGE_LOCAL_SOLR: "true"
    ports:
      - "21000:21000"
    restart: unless-stopped
    # Give atlas_stop.py time to stop Atlas/Solr/HBase/ZooKeeper cleanly
    stop_grace_period: 2m
    entrypoint: ["/bin/bash", "/atlas-lab/atlas-entrypoint.sh"]
    command: []
    volumes:
      - atlas_data:/opt/apache-atlas-2.1.0/data
      - atlas_logs:/opt/apache-atlas-2.1.0/logs
      # Solr node home: keeps the Lucene indexes (vertex/edge/fulltext)
      # across container recreation. Without this, 'down && up' wipes the
      # indexes while HBase keeps the graph, breaking search silently.
      - atlas_solr:/opt/apache-atlas-2.1.0/solr/server/solr
      - ./atlas-entrypoint.sh:/atlas-lab/atlas-entrypoint.sh:ro
    healthcheck:
      # NOTE: Atlas 2.1.0 has no /api/atlas/v2/admin/* endpoints;
      # admin endpoints are v1 only and return {"Status":"ACTIVE"}
      test: ["CMD-SHELL", "wget -q -O /dev/null --user=admin --password=admin http://localhost:21000/api/atlas/admin/status || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 10m

volumes:
  atlas_data:
  atlas_logs:
  atlas_solr:
```

Design notes (each addresses a real defect — see Appendix A):

| Element | Purpose |
|---|---|
| `atlas_solr` volume | The image stores Solr indexes under `solr/server/solr/`, **not** under `data/`. Without this volume, every `down && up` wipes the indexes while HBase keeps the graph → search APIs silently return nothing. |
| `entrypoint` + `command: []` | Replaces the stock crash-loop-prone entrypoint with the wrapper. `command: []` clears the image's default `Cmd` so it is not passed as arguments. |
| `stop_grace_period: 2m` | `atlas_stop.py` stops four services sequentially; the default 10 s grace would SIGKILL mid-shutdown. |
| `healthcheck` | Readiness probe against the **v1** admin status endpoint. The image ships **busybox wget**, which has no `-u` flag — hence `--user/--password`. |
| no `version:` key | Obsolete in Compose v2 (prints a warning). |

### B.3 `typedef.json` — custom type definition

```json
{
  "entityDefs": [
    {
      "name": "my_custom_table",
      "superTypes": ["DataSet"],
      "serviceType": "custom_lab",
      "typeVersion": "1.0",
      "attributeDefs": [
        {
          "name": "owner",
          "typeName": "string",
          "isOptional": true,
          "cardinality": "SINGLE"
        },
        {
          "name": "data_tier",
          "typeName": "string",
          "isOptional": true,
          "cardinality": "SINGLE"
        }
      ]
    }
  ]
}
```

### B.4 `entity.json` — entity creation payload

```json
{
  "entity": {
    "typeName": "my_custom_table",
    "attributes": {
      "name": "orders_raw",
      "qualifiedName": "raw_zone.orders_raw@lab_cluster",
      "description": "Raw ingestion table for retail orders",
      "owner": "DataEngineeringTeam",
      "data_tier": "Raw"
    }
  }
}
```

> `qualifiedName` is the **unique** identity attribute for `DataSet` sub-types — pick one value and reuse it for updates.

### B.5 `entity-update.json` — entity update payload

Kept separate from `entity.json` so both payloads stay reusable. The `guid` is filled in at runtime (see Part E).

```json
{
  "entity": {
    "guid": "PASTE_YOUR_RETURNED_GUID_HERE",
    "typeName": "my_custom_table",
    "attributes": {
      "name": "orders_raw",
      "qualifiedName": "raw_zone.orders_raw@lab_cluster",
      "owner": "AnalyticsArchitectureTeam"
    }
  }
}
```

---

## Part C — Deploy the Lab

```bash
cd ~/atlas_api_demo        # this folder
docker compose up -d       # pulls the image on first run (~2.7 GB)
```

Expected output:
```
[+] Running 3/3
 ✔ Network atlas_api_demo_default   Created
 ✔ Volume atlas_api_demo_atlas_...  Created
 ✔ Container apache-atlas-lab       Started
```

---

## Part D — Verify Readiness

Atlas bootstraps HBase regions, Solr collections, Kafka and its Spring context on every boot — this takes **2–5 minutes** (~1 minute on a fast VM).

### D.1 Watch the logs (optional but informative)

```bash
docker logs -f apache-atlas-lab
```

Normal boot sequence:
```
[atlas-lab-entrypoint] starting Atlas stack...
configured for local hbase.
hbase started.
configured for local solr.
solr started.
setting up solr collections...
starting atlas on host localhost
starting atlas on port 21000
<Atlas banner + Spring/application log lines>
Apache Atlas Server process started!     ← THE readiness marker
Running Apache Atlas with PID <pid>...
```

**What to expect — and what NOT to worry about:**
* The definitive marker is **`Apache Atlas Server process started!`**. It is only visible because the wrapper runs Python unbuffered (`python -u`); with the stock entrypoint it never appears.
* The strings `Apache Atlas Server started!!!` and `oejs.Server:main: Started @XXXXXms` (quoted in older versions of this runbook) are **never emitted** by this image — do not wait for them.
* `Collection 'vertex_index' already exists!` / `Collection 'edge_index' already exists!` errors during startup are **normal** for this image (the Solr cores ship pre-created inside it) and can be ignored.
* A single `java.net.ConnectException: Connection refused` line early in the boot is a transient internal poll — harmless.

### D.2 The reliable readiness check

Either watch the container flip to `(healthy)`:
```bash
docker compose ps
# STATUS: Up 1 minute (healthy)
```

…or poll the status endpoint yourself:
```bash
until curl -s -u admin:admin http://localhost:21000/api/atlas/admin/status | grep -q ACTIVE; do
  sleep 5; echo "waiting for Atlas..."
done && echo "Atlas is ready"
```

### D.3 Web UI

Browse to `http://<VM_IP>:21000` and log in with **admin / admin**. (Open port 21000 in your security group/firewall for off-VM access.)

---

## Part E — Test the Atlas REST APIs

### E.0 Automated smoke test (recommended first)

```bash
./smoke_test.sh
```

The script is **idempotent** — safe to run any number of times. It:
1. Health-checks the server (v1 admin status)
2. Registers `my_custom_table` **only if missing** (GET-then-POST)
3. Creates the `orders_raw` entity **only if missing** (lookup by unique `qualifiedName`)
4. Updates the owner via `guid` **only if the value changed** (Atlas emits no `UPDATE` mutation for a no-op POST)
5. Verifies the update persisted and the **Solr index is consistent** (search by type)

Expected output (fresh lab):
```
==> [1/5] Health check: GET /api/atlas/admin/status
    response: {"Status":"ACTIVE"}
==> [2/5] Ensure entity type 'my_custom_table' is registered
    registered via POST /api/atlas/v2/types/typedefs
==> [3/5] Ensure entity 'raw_zone.orders_raw@lab_cluster' exists
    created via POST /api/atlas/v2/entity, guid=<guid>
==> [4/5] Update entity owner via guid
    update accepted (mutatedEntities.UPDATE)
==> [5/5] Verify update persisted and search index is consistent
    GET by guid OK: name=orders_raw owner=AnalyticsArchitectureTeam data_tier=Raw status=ACTIVE
    search OK: 1 match(es), approximateCount=1

ALL SMOKE TESTS PASSED - guid=<guid>
```

To target a remote VM: `ATLAS_URL=http://<VM_IP>:21000 ./smoke_test.sh`

### E.1 Health check (manual)

```bash
curl -u admin:admin http://localhost:21000/api/atlas/admin/status
```
**Expected:** `{"Status":"ACTIVE"}`

> **IMPORTANT:** Atlas 2.1.0 has **no `/api/atlas/v2/admin/*` endpoints** — the admin resource is registered under the v1 path only. Calling `/api/atlas/v2/admin/status` returns **HTTP 500** with `NotFoundException ... for uri` plus an error ID; the server is fine, the URL simply does not exist in this version. Note also the response key is capitalized: `"Status"`, not `"status"`.

### E.2 Register the custom TypeDef (manual)

```bash
curl -u admin:admin -X POST -H "Content-Type: application/json" \
  -d @typedef.json "http://localhost:21000/api/atlas/v2/types/typedefs"
```
**Expected:** HTTP 200, the `entityDefs` array echoed back with a `guid`, `createdBy: admin`, and the inherited DataSet relationship attributes.

### E.3 Create the entity (manual)

```bash
curl -u admin:admin -X POST -H "Content-Type: application/json" \
  -d @entity.json "http://localhost:21000/api/atlas/v2/entity"
```
**Expected:** HTTP 200 with `mutatedEntities.CREATE[0]` containing your entity and its **`guid`** — record it:
```json
{"mutatedEntities":{"CREATE":[{"guid":"<GUID>","typeName":"my_custom_table",...}]},"guidAssignments":{...}}
```

### E.4 Update the entity via guid (manual)

Put the guid from E.3 into `entity-update.json` (replacing `PASTE_YOUR_RETURNED_GUID_HERE`), then:
```bash
curl -u admin:admin -X POST -H "Content-Type: application/json" \
  -d @entity-update.json "http://localhost:21000/api/atlas/v2/entity"
```
**Expected:** HTTP 200 with `mutatedEntities.UPDATE` present. Attributes not mentioned in the update payload (e.g. `data_tier`) are preserved.

### E.5 Verify persistence and search (manual)

```bash
# Fetch by guid (reads the HBase graph store)
curl -u admin:admin "http://localhost:21000/api/atlas/v2/entity/guid/<GUID>"

# Search by type (reads the Solr index)
curl -u admin:admin "http://localhost:21000/api/atlas/v2/search/basic?typeName=my_custom_table"
```
**Expected:** the first call returns the entity with `owner: AnalyticsArchitectureTeam` and `data_tier: Raw`; the second returns it in `entities` with `approximateCount: 1`.

---

## Part F — Lab Lifecycle Management

| Operation | Command | Notes |
|---|---|---|
| Start | `docker compose up -d` | Boots in ~1–5 min; wait for `(healthy)` |
| Stop (graceful) | `docker compose down` | Takes ~30–60 s (clean 4-service shutdown); data + indexes persist |
| Restart | `docker compose restart` | Graceful stop + start; no stale-state crash loops |
| Full reset | `docker compose down -v` | ⚠️ Wipes ALL volumes — every TypeDef and entity is erased |
| Logs (live) | `docker logs -f apache-atlas-lab` | Streams `application.log` + startup output |
| Status | `docker compose ps` | `(healthy)` = API is serving |
| Resource usage | `docker stats apache-atlas-lab` | Idle lab uses ~2–3 GB RAM |

**Persistence guarantee:** after `down` + `up` (or a VM reboot), the typedef, the entity, **and** search results all survive — verified by re-running `./smoke_test.sh`, which will report `already registered` / `already exists` and still pass every check.

---

## Part G — Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Container restarts forever; logs show `Error while creating ephemeral at /brokers/ids/1 ... does not match current session` / `Fatal error during KafkaServer startup` | Stale ZooKeeper/Kafka state from an **ungraceful** shutdown (happens with the stock image entrypoint, or if the VM was hard-killed mid-boot) | Deployed labs: `docker compose down -v && docker compose up -d` (wipes data). Prevented going forward by the `atlas-entrypoint.sh` wrapper. |
| `HTTP 500` + `NotFoundException ... for uri: .../api/atlas/v2/admin/status` | Endpoint does not exist in Atlas 2.1.0 (admin endpoints are v1-only) | Use `/api/atlas/admin/status`. |
| Waited forever for `Apache Atlas Server started!!!` in `docker logs` | That marker is never printed by this image; the real marker (`Apache Atlas Server process started!`) is hidden by Python stdout buffering under the stock entrypoint | Use the wrapper (`python -u`), or poll the status endpoint / wait for `(healthy)`. |
| Entity exists (GET by guid works) but `/v2/search/basic` returns nothing | Solr indexes were wiped while HBase data persisted — happens if the `atlas_solr` volume is missing | Keep the `atlas_solr` volume; if already broken, `docker compose down -v` and re-run `./smoke_test.sh`. |
| Container stuck at `(health: starting)` forever | Healthcheck uses `wget -u` (unsupported by the image's busybox wget), or Atlas is still booting | Healthcheck must use `--user=... --password=...`; booting takes up to 5 min (`start_period: 10m` covers it). |
| `Collection 'vertex_index' already exists!` during startup | Normal for this image (cores ship pre-created) | Ignore. |
| `docker: permission denied` while running as non-root | User not in the `docker` group | `sudo usermod -aG docker "$USER"` then re-login. |
| Port conflict on 21000 | Another service on the host uses it | Change the mapping in `docker-compose.yml` (e.g. `"21001:21000"`) and use the new host port everywhere. |
| Boot very slow / OOM-killed container | VM below 4 GB RAM | Resize the VM to ≥ 4 GB; check `docker stats` and `dmesg | grep -i oom`. |

**Deep-dive logs inside the container:**
```bash
docker exec -it apache-atlas-lab sh
ls /opt/apache-atlas-2.1.0/logs/          # application.log, audit.log, atlas.*.out/.err
tail -f /opt/apache-atlas-2.1.0/logs/application.log
```

---

## Appendix A — Issues Found & Fixes Applied (execution log)

| # | Issue | Fix |
|---|-------|-----|
| 1 | **Crash-loop after any ungraceful restart** (docker daemon restart, VM reboot, `docker stop`): stale Kafka ephemeral node in the persisted ZooKeeper state causes `Fatal error during KafkaServer startup` → Atlas dies → container restarts forever. | `atlas-entrypoint.sh` traps SIGTERM/SIGINT and runs `atlas_stop.py` (clean Atlas/Solr/HBase/ZK shutdown); `stop_grace_period: 2m`; wrapper exits when Atlas dies so `restart: unless-stopped` self-heals. |
| 2 | **Solr index loss on every `down && up`**: only `data/` and `logs/` were persisted, but Solr keeps its Lucene indexes under `/opt/apache-atlas-2.1.0/solr/server/solr/` — indexes were wiped while HBase kept the graph, silently breaking search. | Added the `atlas_solr` named volume for the Solr node home. |
| 3 | **Wrong health-check URL**: `/api/atlas/v2/admin/status` does not exist in Atlas 2.1.0 (admin endpoints are v1-only); it returns HTTP 500 `NotFoundException` and mimics a broken server. | Use `/api/atlas/admin/status`; expected body `{"Status":"ACTIVE"}` (capital `S`). Compose healthcheck uses the correct URL. |
| 4 | **Startup log markers never appear**: `Apache Atlas Server started!!!` / `oejs.Server:main: Started @XXXXXms` are not emitted by this image; the real marker (`Apache Atlas Server process started!`) was trapped in Python's block-buffered stdout under the stock entrypoint. | Wrapper runs `atlas_start.py` with `python -u`; readiness verified by polling the status endpoint (automated by the compose healthcheck). |
| 5 | **Busybox wget in the image** rejects `-u`, so a "standard" healthcheck would always report unhealthy. | Healthcheck uses `--user=admin --password=admin`. |
| 6 | Obsolete `version:` compose key (warning on Compose v2); stock entrypoint keeps tailing logs after Atlas dies, so a dead Atlas looks like an "Up" container. | Dropped `version:`; wrapper exits when the starter exits so the restart policy re-boots the lab. |
| 7 | Re-running the documented API steps fails (duplicate type/entity, no-op updates return no `UPDATE` mutation). | Added idempotent `smoke_test.sh` (GET-then-POST for typedef and entity, change-detection before update) and documented the manual equivalents. |

**Validated end-to-end (each after a full `down -v` fresh start):** boot → healthy (~50 s) → typedef registration → entity create → entity update via guid → search verification → `down`/`up` cycle → data **and** search still working → graceful restart → zero crash loops → smoke test passes on fresh and existing state.

---

## Appendix B — API Quick Reference (Atlas 2.1.0)

All calls use basic auth `admin:admin` and `Content-Type: application/json` for bodies.

| Purpose | Method & Path |
|---|---|
| Server status (**v1 only!**) | `GET /api/atlas/admin/status` |
| List all typedefs | `GET /api/atlas/v2/types/typedefs` |
| Get typedef by name | `GET /api/atlas/v2/types/typedef/name/{name}` |
| Create typedefs (batch) | `POST /api/atlas/v2/types/typedefs` |
| Create/update entity | `POST /api/atlas/v2/entity` |
| Get entity by guid | `GET /api/atlas/v2/entity/guid/{guid}` |
| Get entity by unique attribute | `GET /api/atlas/v2/entity/uniqueAttribute/type/{type}?attr:qualifiedName={urlEncodedValue}` |
| Basic search by type | `GET /api/atlas/v2/search/basic?typeName={type}` |
| Full-text/DSL search | `GET /api/atlas/v2/search/dsl?query={query}` |

**Version caveat:** v2 admin endpoints (`/api/atlas/v2/admin/*`) exist only in later Atlas releases (2.2+). In 2.1.0, admin operations live under `/api/atlas/admin/*`.
