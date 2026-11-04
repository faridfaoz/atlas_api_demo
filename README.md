# Apache Atlas API Demo

A self-contained lab for learning and testing the **Apache Atlas 2.1.0** REST APIs. It runs Atlas (with its embedded HBase, ZooKeeper, Kafka, and Solr) in a single Docker container, then walks its v2 REST API end to end: register a custom metadata type, create and update a metadata entity, and verify Solr-indexed search.

## What you end up with

- A running Atlas server (Web UI + REST API) on port `21000`
- A custom entity type (`my_custom_table`) registered via the Types API
- A metadata entity created, updated, and searchable through the v2 API
- Clean start/stop/restart behavior with persistent data and search index

## Architecture

```
┌─────────────────────── apache-atlas-lab container ───────────────────────┐
│  Apache Atlas 2.1.0 (Jetty, port 21000)                                  │
│    ├── embedded HBase        → graph/metadata store   (volume: atlas_data)│
│    ├── embedded ZooKeeper    → HBase + Kafka coordination                │
│    ├── embedded Kafka        → Atlas notification bus                    │
│    └── embedded Solr         → search/indexing        (volume: atlas_solr)│
└──────────────────────────────────────────────────────────────────────────┘
          :21000 → host        admin / admin
```

Named Docker volumes keep the graph data, Solr indexes, and logs across restarts. `docker compose down -v` wipes everything.

## Requirements

- Linux with Docker Engine + Docker Compose v2
- ≥ 2 vCPUs, 4–8 GB RAM, ~5 GB free disk (image is ~2.7 GB)
- `curl` and `python3` for the smoke test

## Quick start

```bash
docker compose up -d
docker compose logs -f   # wait for "Apache Atlas Server process started!" (first boot: 2–5 min)
```

Then run the idempotent end-to-end smoke test:

```bash
./smoke_test.sh
# or against a remote host:
ATLAS_URL=http://VM_IP:21000 ./smoke_test.sh
```

The smoke test exercises five steps: health check (`GET /api/atlas/admin/status`), typedef registration (`POST /api/atlas/v2/types/typedefs`), entity creation (`POST /api/atlas/v2/entity`), entity update by guid, and verification that the update persisted and the Solr search index is consistent (`GET /api/atlas/v2/search/basic`).

Open the Web UI at http://localhost:21000 (admin/admin) to browse the results.

## Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | Deployment manifest: ports, volumes, healthcheck, graceful stop |
| `atlas-entrypoint.sh` | Entrypoint wrapper that traps SIGTERM and stops the embedded stack cleanly (avoids the stale Kafka/ZooKeeper ephemeral-node crash loop) |
| `typedef.json` | Custom type definition payload (`my_custom_table`, extends `DataSet`) |
| `entity.json` | Entity creation payload (`raw_zone.orders_raw@lab_cluster`) |
| `entity-update.json` | Entity update payload (change owner) |
| `smoke_test.sh` | Idempotent end-to-end API verification script |
| `docker_atlas_setup.md` | Full runbook: setup from a fresh VM, API walkthrough, troubleshooting, and the defects found and fixed along the way |

## Notes

- Atlas 2.1.0 has no v2 admin endpoints — health checks must use the v1 `GET /api/atlas/admin/status`, which returns `{"Status":"ACTIVE"}`.
- The image ships busybox `wget`, so the container healthcheck uses `--user/--password` (the `-u` flag is unsupported).
- `qualifiedName` is the unique attribute for entities; the smoke test uses it for idempotent lookups.

See [docker_atlas_setup.md](docker_atlas_setup.md) for the complete runbook, curl-by-curl API walkthrough, and troubleshooting guide.
