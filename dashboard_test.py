#!/usr/bin/env python3
"""Automated end-to-end validation of the Atlas Streamlit dashboard.

Runs the dashboard through Streamlit's AppTest framework (no browser needed)
and exercises every tab against the live Atlas lab:

  1. Status button          -> expects State: ACTIVE
  2. Tab 1 basic search     -> expects the orders_raw entity + GUID inspector
  3. Tab 2 typedef POST     -> expects 200 (probe type, deleted afterwards)
  4. Tab 3 entity POST      -> expects 200 (update of orders_raw, no new entity)

Idempotent: safe to re-run. The part-1 smoke test restores the owner
attribute afterwards.

Usage:
    .venv/bin/python dashboard_test.py
    ATLAS_URL=http://VM_IP:21000 .venv/bin/python dashboard_test.py
"""
import json
import os
import sys
from pathlib import Path

import requests
from streamlit.testing.v1 import AppTest

ATLAS = os.environ.get("ATLAS_URL", "http://localhost:21000")
AUTH = ("admin", "admin")
APP = Path(__file__).resolve().parent / "app.py"

ok = True


def check(label, cond, detail=""):
    global ok
    status = "PASS" if cond else "FAIL"
    if not cond:
        ok = False
    print(f"[{status}] {label} {detail}")


at = AppTest.from_file(str(APP), default_timeout=60)
at.run()

# --- 1. Server status button -------------------------------------------------
at.button(key="status_btn").click()
at.run()
msgs = [s.value for s in at.success]
check("status button shows ACTIVE",
      any("ACTIVE" in m for m in msgs), f"-> {msgs}")

# --- 2. Tab 1: basic search ---------------------------------------------------
at.selectbox[0].set_value("my_custom_table")
at.run()
at.button[1].click()  # "Run Basic Search"
at.run()
msgs = [s.value for s in at.success]
check("search succeeds", any("Found" in m for m in msgs), f"-> {msgs}")
df = at.dataframe[0].value
check("orders_raw present in results",
      "raw_zone.orders_raw@lab_cluster" in set(df["QualifiedName"]),
      f"-> {list(df['QualifiedName'])}")
check("GUID inspector selectbox rendered", len(at.selectbox) >= 2,
      f"-> {len(at.selectbox)} selectboxes")
check("raw JSON payload rendered", len(at.json) >= 1,
      f"-> {len(at.json)} json elements")

# --- 3. Tab 2: typedef POST (probe type, no entities) -------------------------
# Pre-cleanup: remove the probe type from any previous run
# (registering an existing type again fails with HTTP 409).
requests.delete(f"{ATLAS}/api/atlas/v2/types/typedef/name/probe_dash_type",
                auth=AUTH, timeout=10)
probe_typedef = {
    "entityDefs": [{
        "name": "probe_dash_type",
        "superTypes": ["DataSet"],
        "serviceType": "custom_lab",
        "typeVersion": "1.0",
        "attributeDefs": [
            {"name": "owner", "typeName": "string", "isOptional": True, "cardinality": "SINGLE"}
        ],
    }]
}
at.text_area[0].set_value(json.dumps(probe_typedef, indent=2))
at.run()
at.button[2].click()  # "Submit Definition Scheme"
at.run()
msgs = [s.value for s in at.success]
check("typedef POST succeeds", any("registered successfully" in m for m in msgs), f"-> {msgs}")

# --- 4. Tab 3: entity POST (update of the existing orders_raw entity) ---------
# Uses the existing entity's guid so no new entity is created; the part-1
# smoke test restores the owner value afterwards.
guid = requests.get(
    f"{ATLAS}/api/atlas/v2/entity/uniqueAttribute/type/my_custom_table",
    auth=AUTH,
    params={"attr:qualifiedName": "raw_zone.orders_raw@lab_cluster"},
    timeout=10,
).json()["entity"]["guid"]
probe_entity = {
    "entity": {
        "guid": guid,
        "typeName": "my_custom_table",
        "attributes": {
            "name": "orders_raw",
            "qualifiedName": "raw_zone.orders_raw@lab_cluster",
            "owner": "ValidationTeam",
        },
    }
}
at.text_area[1].set_value(json.dumps(probe_entity, indent=2))
at.run()
at.button[3].click()  # "Commit Asset Entity Registry Entry"
at.run()
msgs = [s.value for s in at.success]
check("entity POST succeeds",
      any("transaction logged successfully" in m for m in msgs), f"-> {msgs}")

# Verify the update persisted
owner = requests.get(f"{ATLAS}/api/atlas/v2/entity/guid/{guid}", auth=AUTH, timeout=10) \
    .json()["entity"]["attributes"]["owner"]
check("update persisted in Atlas", owner == "ValidationTeam", f"-> owner={owner}")

# --- Cleanup: remove the probe type -------------------------------------------
code = requests.delete(f"{ATLAS}/api/atlas/v2/types/typedef/name/probe_dash_type",
                       auth=AUTH, timeout=10).status_code
check("probe type cleanup", code in (200, 204, 404), f"-> HTTP {code}")

print()
print("RESULT:", "ALL DASHBOARD CHECKS PASSED" if ok else "SOME CHECKS FAILED")
print("Restore lab state with: ./smoke_test.sh")
sys.exit(0 if ok else 1)
