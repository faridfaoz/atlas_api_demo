# Runbook: Apache Atlas Dashboard Lab (Part 2) — Streamlit Visualization

Part 2 of the Atlas API lab: a **Streamlit** dashboard that visualizes and operates the Apache Atlas lab from Part 1. The dashboard talks to the Atlas v2 REST API and lets you check server status, search and inspect metadata entities, register custom type definitions, and post entity payloads — all from the browser.

Follow this runbook top-to-bottom on the VM where the Part 1 lab is already running and you will end up with:

* A Python virtual environment (`.venv/`) containing Streamlit and its dependencies — nothing installed system-wide
* The dashboard (`app.py`) running on port `8501`
* An automated, idempotent end-to-end validation (`dashboard_test.py`) that exercises every tab against the live Atlas instance

> **Provenance:** the dashboard is based on a draft script (`atlas-streamlit-dashboard.md`, provided separately). The draft contained **fatal defects** — it would not even parse, and its health check targeted an endpoint that does not exist in Atlas 2.1.0. All defects were found during validation, fixed in `app.py`, and are documented in **Appendix A**. The validation run is recorded in **Part D**. All files referenced here exist in this folder; the inline contents allow rebuilding everything from this document alone.

---

## 1. Architecture Overview

```
┌──────────────┐  HTTP :8501  ┌──────────────────────────┐  HTTP :21000  ┌───────────────────────┐
│  Browser     │ ───────────► │  Streamlit dashboard     │ ────────────► │  Apache Atlas lab     │
│  (you)       │              │  app.py (venv)           │  admin/admin  │  (Part 1, Docker)     │
└──────────────┘              └──────────────────────────┘               └───────────────────────┘
```

The dashboard is a **stateless REST client**: all state lives in Atlas. Streamlit re-runs `app.py` on every widget interaction, which is why every action is a plain `requests` call — no session or database of its own.

**Resource needs:** negligible (~200 MB RAM for the Streamlit server). The heavy part is the Atlas container from Part 1.

---

## 2. Prerequisites

| Requirement | Check command | Notes |
|---|---|---|
| Part 1 lab running and healthy | `docker compose ps` | See `docker_atlas_setup.md`; `smoke_test.sh` must pass |
| Python 3.10+ | `python3 --version` | Python 3.12 tested |
| `python3-venv` package | `python3 -m venv --help` | `apt-get install -y python3.12-venv` if missing (adjust to your version) |
| Port `8501` free | `ss -ltn \| grep 8501` | Streamlit port; open the firewall only if accessing off-VM |

---

## Part A — Python Virtual Environment

All Python dependencies live in a project-local venv. **Nothing is installed with system pip.**

```bash
cd atlas_api_demo

# Create the venv (one-time)
python3 -m venv .venv

# Activate it (every new shell)
source .venv/bin/activate

# Install the dashboard dependencies
pip install --upgrade pip
pip install streamlit requests pandas
```

Verify:

```bash
(.venv) $ python -c "import streamlit, pandas, requests; print('deps OK')"
(.venv) $ which streamlit   # must point into .venv/bin
```

> If `python3 -m venv` fails with `ensurepip is not installed`, run
> `sudo apt-get install -y python3.12-venv` (or the version-matching package) first.
>
> The `.venv/` directory is disposable: delete it and repeat this part to rebuild from scratch. It is excluded from git via `.gitignore`.

---

## Part B — The Dashboard Application (`app.py`)

Save the following as `app.py` in the project folder (identical to the file in this repo):

```python
import streamlit as st
import requests
import json
import pandas as pd

# Configure the page layout
st.set_page_config(
    page_title="Apache Atlas Lab Dashboard",
    page_icon="📊",
    layout="wide"
)

# Application Title and Setup Context
st.title("📊 Apache Atlas Metadata Test Lab")
st.markdown("Interact with the Apache Atlas REST API V2 to query system status, browse entity configurations, and upload testing payloads.")

# Sidebar Configuration for API Access
st.sidebar.header("🔌 Connection Settings")
atlas_url = st.sidebar.text_input("Atlas Host URL", value="http://localhost:21000")
username = st.sidebar.text_input("Username", value="admin")
password = st.sidebar.text_input("Password", value="admin", type="password")

auth = (username, password)
headers = {"Content-Type": "application/json"}

# --- SYSTEM HEALTH CHECK STATUS ---
st.subheader("📡 Server Status Tracking")

if st.button("🔄 Check Atlas Server Status", key="status_btn"):
    try:
        # Atlas 2.1.0 has no v2 admin endpoints; the status endpoint is v1
        # and returns {"Status":"ACTIVE"} (capital S).
        status_endpoint = f"{atlas_url}/api/atlas/admin/status"
        response = requests.get(status_endpoint, auth=auth, timeout=5)

        if response.status_code == 200:
            server_state = response.json().get("Status", "UNKNOWN")
            if server_state == "ACTIVE":
                st.success(f"🟢 Connected successfully! Server State: **{server_state}**")
            else:
                st.warning(f"🟡 Server responded with state: **{server_state}**")
        else:
            st.error(f"🔴 Connection failed. HTTP Status Code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        st.error(f"🔴 Could not reach server connection endpoint. Error: {e}")

st.markdown("---")

# --- MAIN DASHBOARD WORKSPACES ---
tab1, tab2, tab3 = st.tabs(["🔍 Search & Inspect Metadata", "🏗️ Create Custom Type (TypeDef)", "📥 Post Entity Asset"])

# TAB 1: SEARCH & INSPECT METADATA
with tab1:
    st.header("Search & Inspect System Metadata")

    col1, col2 = st.columns([1, 3])

    with col1:
        search_type = st.selectbox("Entity Type Filter", ["DataSet", "my_custom_table", "Process", "Infrastructure"])
        limit = st.slider("Max Record Fetch Limit", 5, 50, 10)
        trigger_search = st.button("🚀 Run Basic Search")

    with col2:
        if trigger_search:
            search_endpoint = f"{atlas_url}/api/atlas/v2/search/basic"
            params = {"typeName": search_type, "limit": limit}

            with st.spinner("Querying Atlas Catalog..."):
                try:
                    res = requests.get(search_endpoint, auth=auth, params=params, headers=headers)
                    if res.status_code == 200:
                        data = res.json()
                        entities = data.get("entities", [])

                        if entities:
                            st.success(f"Found {len(entities)} tracking matching entries.")

                            # Flattening results for table representation
                            flattened_data = []
                            for ent in entities:
                                flattened_data.append({
                                    "GUID": ent.get("guid"),
                                    "TypeName": ent.get("typeName"),
                                    "Name": ent.get("attributes", {}).get("name"),
                                    "QualifiedName": ent.get("attributes", {}).get("qualifiedName"),
                                    "Status": ent.get("status")
                                })

                            df = pd.DataFrame(flattened_data)
                            st.dataframe(df, width="stretch")

                            # Deep inspect single configuration element
                            st.markdown("### 🔬 Inspect Raw Data JSON Payload")
                            selected_guid = st.selectbox("Select Target GUID to Inspect", df["GUID"].tolist())

                            if selected_guid:
                                entity_endpoint = f"{atlas_url}/api/atlas/v2/entity/guid/{selected_guid}"
                                entity_res = requests.get(entity_endpoint, auth=auth, headers=headers)
                                if entity_res.status_code == 200:
                                    st.json(entity_res.json())
                        else:
                            st.info(f"No existing active entities found matching type: `{search_type}`")
                    else:
                        st.error(f"Search failed. Error code {res.status_code}: {res.text}")
                except Exception as e:
                    st.error(f"Request execution failed: {e}")

# TAB 2: CREATE CUSTOM TYPE
with tab2:
    st.header("Define a Custom Metadata Entity Blueprint")
    st.markdown("Register custom schema definitions (TypeDefs) directly into your active lab instance cluster.")

    default_typedef = {
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
              "isOptional": True,
              "cardinality": "SINGLE"
            },
            {
              "name": "data_tier",
              "typeName": "string",
              "isOptional": True,
              "cardinality": "SINGLE"
            }
          ]
        }
      ]
    }

    typedef_input = st.text_area("TypeDef JSON Definition Specification Target", value=json.dumps(default_typedef, indent=2), height=350)

    if st.button("📤 Submit Definition Scheme"):
        try:
            payload = json.loads(typedef_input)
            typedef_endpoint = f"{atlas_url}/api/atlas/v2/types/typedefs"

            res = requests.post(typedef_endpoint, auth=auth, json=payload, headers=headers)

            if res.status_code == 200:
                st.success("🎉 Type Definition submitted and registered successfully!")
                st.json(res.json())
            else:
                st.error(f"Submission Rejected ({res.status_code}): {res.text}")
        except json.JSONDecodeError:
            st.error("❌ Invalid JSON formatting schema. Please validate syntax configuration entries.")
        except Exception as e:
            st.error(f"Execution handling crash event: {e}")

# TAB 3: POST ENTITY ASSET
with tab3:
    st.header("Instantiate Metadata Asset Tracking Instance")
    st.markdown("Populate testing metrics entries or patch modification updates against current schemas using runtime references.")

    default_entity = {
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

    entity_input = st.text_area("Asset Instance Payload Specification", value=json.dumps(default_entity, indent=2), height=300)

    if st.button("📥 Commit Asset Entity Registry Entry"):
        try:
            payload = json.loads(entity_input)
            entity_endpoint = f"{atlas_url}/api/atlas/v2/entity"

            res = requests.post(entity_endpoint, auth=auth, json=payload, headers=headers)

            if res.status_code == 200:
                st.success("🎉 Entity registry transaction logged successfully!")
                response_data = res.json()
                st.json(response_data)

                # Highlight the generated GUID explicitly to help tracing routines
                guid_assignments = response_data.get("guidAssignments", {})
                if guid_assignments:
                    st.info(f"💡 Key Reference Tracker - Assigned Guid Mapping Index: `{guid_assignments}`")
            else:
                st.error(f"Transaction Rejected ({res.status_code}): {res.text}")
        except json.JSONDecodeError:
            st.error("❌ Invalid JSON formatting schema. Please validate syntax configuration entries.")
        except Exception as e:
            st.error(f"Execution handling crash event: {e}")
```

---

## Part C — Launch the Dashboard

```bash
source .venv/bin/activate
streamlit run app.py
```

Streamlit prints the local URL and opens nothing else. On a headless VM, use:

```bash
streamlit run app.py --server.headless true --server.port 8501
```

Verify from the VM:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8501   # expect 200
```

Then open **http://VM_IP:8501** in your browser (open port `8501` in the firewall/security group if accessing off-VM).

To stop the dashboard: `Ctrl+C` in the foreground terminal (or `pkill -f "streamlit run app.py"` for a background run).

---

## Part D — Validate the Dashboard

### D.1 Automated validation (recommended)

`dashboard_test.py` (in this folder, inlined in **Appendix B**) drives the real `app.py` through Streamlit's `AppTest` framework — no browser needed — and exercises every tab against the live Atlas instance:

| # | Check | Expected |
|---|---|---|
| 1 | Status button | Success message with `State: **ACTIVE**` |
| 2 | Tab 1 search for `my_custom_table` | `orders_raw` entity in the results table, GUID inspector + raw JSON rendered |
| 3 | Tab 2 typedef POST (probe type `probe_dash_type`) | `registered successfully` (HTTP 200); probe type deleted afterwards |
| 4 | Tab 3 entity POST (update of `orders_raw` by guid) | `transaction logged successfully` (HTTP 200); owner change persisted in Atlas |

Run it:

```bash
.venv/bin/python dashboard_test.py
```

Recorded output of the validation run on this lab (Ubuntu 24.04, Python 3.12.3, Streamlit 1.66.1, pandas 3.1.1, requests 2.34.2):

```
[PASS] status button shows ACTIVE -> ['Connected successfully! Server State: **ACTIVE**']
[PASS] search succeeds -> ['Found 3 tracking matching entries.']
[PASS] orders_raw present in results -> ['probe_zone.probe_dash@lab_cluster', 'probe_zone.probe_dash@lab_cluster', 'raw_zone.orders_raw@lab_cluster']
[PASS] GUID inspector selectbox rendered -> 2 selectboxes
[PASS] raw JSON payload rendered -> 1 json elements
[PASS] typedef POST succeeds -> ['Type Definition submitted and registered successfully!']
[PASS] entity POST succeeds -> ['Entity registry transaction logged successfully!']
[PASS] update persisted in Atlas -> owner=ValidationTeam
[PASS] probe type cleanup -> HTTP 204
RESULT: ALL DASHBOARD CHECKS PASSED
```

> The two `probe_zone.probe_dash@lab_cluster` rows in the search results are `DELETED` leftovers from earlier validation runs — see the note in **Part E, Tab 1**. On a fresh lab only `orders_raw` appears.

The test is **idempotent** but its step 4 temporarily sets `owner=ValidationTeam` on `orders_raw`. Restore the Part 1 state afterwards:

```bash
./smoke_test.sh   # resets owner to AnalyticsArchitectureTeam and re-verifies the lab
```

### D.2 Manual click-through (optional)

1. Open http://localhost:8501 → click **🔄 Check Atlas Server Status** → green `ACTIVE` banner.
2. Tab **🔍 Search & Inspect Metadata** → type `my_custom_table`, limit 10 → **🚀 Run Basic Search** → table with `orders_raw`, pick its GUID → raw JSON payload shown.
3. Tab **🏗️ Create Custom Type (TypeDef)** → leave the default JSON (already registered in Part 1) → **📤 Submit** → red banner `Submission Rejected (409): ... already exists` — this is correct Atlas behavior for a duplicate type.
4. Tab **📥 Post Entity Asset** → edit the JSON's `owner` to a new value, add the entity's `guid` (from Tab 1) → **📥 Commit** → green banner with `mutatedEntities.UPDATE`.

---

## Part E — Using the Dashboard

**Sidebar** sets the Atlas URL and credentials for every request (defaults: `http://localhost:21000`, `admin`/`admin`). Change them to point the same dashboard at any Atlas instance.

**Tab 1 — Search & Inspect.** Runs `GET /api/atlas/v2/search/basic?typeName=<type>&limit=<n>` and flattens the results into a table (GUID, TypeName, Name, QualifiedName, Status). Selecting a GUID fetches `GET /api/atlas/v2/entity/guid/{guid}` and pretty-prints the full JSON.

> **Note:** basic search **includes soft-deleted entities** unless `excludeDeletedEntities=true` is passed — the dashboard does not pass it, so deleted entities show up with `Status: DELETED`. This is Atlas behavior, not a dashboard bug (the Part 1 smoke test passes `excludeDeletedEntities=true`, which is why its counts may differ).

**Tab 2 — Create Custom Type.** POSTs the JSON to `GET /api/atlas/v2/types/typedefs`. On success Atlas returns **HTTP 200** with the registered definition. Registering a type that already exists fails with **HTTP 409** `ATLAS-409-00-001` ("Given type ... already exists") — check first via `GET /api/atlas/v2/types/typedef/name/{name}` (the Part 1 smoke test does exactly this).

**Tab 3 — Post Entity Asset.** POSTs the JSON to `POST /api/atlas/v2/entity`. Two modes:
* **Create** (no `guid` in the payload): Atlas assigns a GUID; the response contains `mutatedEntities.CREATE` and `guidAssignments`. Atlas does **not** deduplicate on `qualifiedName` — re-posting the same payload creates a second entity.
* **Update** (existing `guid` in the payload): returns `mutatedEntities.UPDATE`, like the Part 1 `entity-update.json` flow.

> **Note on deletes:** `DELETE /api/atlas/v2/entity/guid/{guid}` is a **soft delete** (status → `DELETED`). The `?purge=true` parameter is accepted in Atlas 2.1.0 but is a **no-op** — deleted entities remain in the graph and in basic search results. Plan lab data accordingly.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `streamlit: command not found` | venv not activated | `source .venv/bin/activate` (or use `.venv/bin/streamlit`) |
| `ensurepip is not installed` when creating the venv | `python3-venv` package missing | `sudo apt-get install -y python3.12-venv` (match your Python version) |
| Status button shows `Connection failed. HTTP 500` | Dashboard pointed at `/api/atlas/v2/admin/status` (the draft's bug) or Atlas not fully booted | Use the fixed `app.py`; wait for `docker compose ps` to report `healthy` |
| Status shows `UNKNOWN` state | Reading the wrong JSON key (the draft's bug — response is `{"Status":"ACTIVE"}`) | Use the fixed `app.py` |
| Tab 2 red banner `409 ... already exists` | Type already registered | Expected Atlas behavior — inspect it via Tab 1 or the Types API instead |
| Search returns fewer rows than expected | Deleted entities excluded, or Solr index lag after a restart | Check the `Status` column; wait ~30 s after writes for the index |
| `ConnectionError` on every action | Atlas container down or wrong URL in the sidebar | `docker compose up -d`, check `docker compose ps`; URL must not end with `/` |
| Port `8501` already in use | Another Streamlit instance | `pkill -f "streamlit run"` or use `--server.port 8502` |
| `use_container_width` warning in the console | Old Streamlit API (the draft's code) | Fixed in `app.py` (`width="stretch"`); harmless if you see it on older code |

---

## Appendix A — Defects Found in the Draft Script and Fixes

The draft (`atlas-streamlit-dashboard.md`) was validated end-to-end before use. The following defects were found and fixed in `app.py`:

| # | Defect (draft) | Impact | Fix (`app.py`) |
|---|---|---|---|
| 1 | `if res.status_code in:` — incomplete `in` operator (twice, in Tab 2 and Tab 3) | **Fatal**: script does not parse; `streamlit run` fails immediately | `if res.status_code == 200:` (Atlas returns 200 on success — verified) |
| 2 | Status check calls `GET /api/atlas/v2/admin/status` | **Always fails**: Atlas 2.1.0 has no v2 admin endpoints (returns HTTP 500) | `GET /api/atlas/admin/status` (v1 — same quirk documented in Part 1's healthcheck) |
| 3 | Reads `response.json().get("status")` | Would always show `UNKNOWN` — the actual response key is `Status` (capital S) | `.get("Status", "UNKNOWN")` |
| 4 | `st.dataframe(df, use_container_width=True)` | Deprecated API, removal date already passed | `st.dataframe(df, width="stretch")` |
| 5 | Typo `Quering` in the search spinner | Cosmetic | `Querying` |

Behavioral observations (kept as-is in `app.py`, documented in Part E): basic search includes soft-deleted entities; duplicate typedef registration returns 409; `?purge=true` is a no-op in Atlas 2.1.0.

## Appendix B — Automated Validation Script (`dashboard_test.py`)

The file is in this folder. Run with `.venv/bin/python dashboard_test.py` (see Part D). It requires the Part 1 lab running with the `my_custom_table` type and the `orders_raw` entity present (i.e., `smoke_test.sh` has passed at least once).
