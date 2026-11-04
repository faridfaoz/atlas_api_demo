#!/usr/bin/env bash
# =============================================================================
# Atlas Lab API Smoke Test
# Idempotent end-to-end verification of the Apache Atlas lab: safe to re-run.
# Exercises: health check, typedef registration, entity create, entity update,
# and Solr-indexed search (v2 API).
#
# Usage:
#   ./smoke_test.sh                          # against http://localhost:21000
#   ATLAS_URL=http://VM_IP:21000 ./smoke_test.sh
# Requires: curl, python3
# =============================================================================
set -euo pipefail

ATLAS_URL="${ATLAS_URL:-http://localhost:21000}"
CRED="${CRED:-admin:admin}"
TYPE="my_custom_table"
QUALIFIED_NAME="raw_zone.orders_raw@lab_cluster"

step() { printf '==> [%s] %s\n' "$1" "$2"; }
fail() { printf '    FAIL: %s\n' "$1"; exit 1; }

# --- 1. Health check (v1 admin status - v2 admin endpoints do NOT exist) -----
step 1/5 "Health check: GET /api/atlas/admin/status"
status=$(curl -s -m 10 -u "$CRED" "$ATLAS_URL/api/atlas/admin/status") || fail "connection refused - is the lab up? (docker compose ps)"
echo "    response: $status"
echo "$status" | grep -q '"Status":"ACTIVE"' || fail "Atlas is not ACTIVE"
echo "    OK"

# --- 2. Typedef: register only if missing -------------------------------------
step 2/5 "Ensure entity type '$TYPE' is registered"
code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' -u "$CRED" \
  "$ATLAS_URL/api/atlas/v2/types/typedef/name/$TYPE")
if [ "$code" = "200" ]; then
  echo "    already registered, skipping POST"
elif [ "$code" = "404" ]; then
  resp=$(curl -s -m 30 -u "$CRED" -X POST -H "Content-Type: application/json" \
    -d @typedef.json "$ATLAS_URL/api/atlas/v2/types/typedefs")
  echo "$resp" | grep -q "\"name\":\"$TYPE\"" || fail "typedef registration failed: $resp"
  echo "    registered via POST /api/atlas/v2/types/typedefs"
else
  fail "unexpected HTTP $code checking typedef"
fi
echo "    OK"

# --- 3. Entity: create only if missing (qualifiedName is unique) --------------
step 3/5 "Ensure entity '$QUALIFIED_NAME' exists"
lookup_url="$ATLAS_URL/api/atlas/v2/entity/uniqueAttribute/type/$TYPE?attr:qualifiedName=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$QUALIFIED_NAME")"
code=$(curl -s -o /tmp/atlas_lookup.json -m 10 -w '%{http_code}' -u "$CRED" "$lookup_url")
if [ "$code" = "200" ]; then
  guid=$(python3 -c "import json; print(json.load(open('/tmp/atlas_lookup.json'))['entity']['guid'])")
  echo "    already exists, guid=$guid"
elif [ "$code" = "404" ]; then
  resp=$(curl -s -m 30 -u "$CRED" -X POST -H "Content-Type: application/json" \
    -d @entity.json "$ATLAS_URL/api/atlas/v2/entity")
  guid=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['mutatedEntities']['CREATE'][0]['guid'])") \
    || fail "entity creation failed: $resp"
  echo "    created via POST /api/atlas/v2/entity, guid=$guid"
else
  fail "unexpected HTTP $code looking up entity"
fi
echo "    OK"

# --- 4. Update entity owner via guid (skip if already at target value) -------
step 4/5 "Update entity owner via guid"
target_owner=$(python3 -c "import json; print(json.load(open('entity-update.json'))['entity']['attributes']['owner'])")
curl -s -m 10 -u "$CRED" "$ATLAS_URL/api/atlas/v2/entity/guid/$guid" > /tmp/atlas_before.json
current_owner=$(python3 -c "import json; print(json.load(open('/tmp/atlas_before.json'))['entity']['attributes']['owner'])")
if [ "$current_owner" = "$target_owner" ]; then
  echo "    owner already '$target_owner', skipping POST"
else
  payload=$(python3 - "$guid" <<'EOF'
import json, sys
d = json.load(open('entity-update.json'))
d['entity']['guid'] = sys.argv[1]
print(json.dumps(d))
EOF
)
  resp=$(curl -s -m 30 -u "$CRED" -X POST -H "Content-Type: application/json" \
    -d "$payload" "$ATLAS_URL/api/atlas/v2/entity")
  echo "$resp" | grep -q '"UPDATE"' || fail "entity update failed: $resp"
  echo "    update accepted (mutatedEntities.UPDATE)"
fi
echo "    OK"

# --- 5. Verify update + Solr-indexed search -----------------------------------
step 5/5 "Verify update persisted and search index is consistent"
curl -s -m 10 -u "$CRED" "$ATLAS_URL/api/atlas/v2/entity/guid/$guid" > /tmp/atlas_verify.json
python3 - "$QUALIFIED_NAME" "$target_owner" <<'EOF' || exit 1
import json, sys
qn, target = sys.argv[1], sys.argv[2]
e = json.load(open('/tmp/atlas_verify.json'))['entity']
a = e['attributes']
assert a['owner'] == target, f"owner is {a['owner']!r}, expected {target!r}"
assert a.get('data_tier') == 'Raw', f"data_tier lost: {a.get('data_tier')!r}"
assert e['status'] == 'ACTIVE'
print(f"    GET by guid OK: name={a['name']} owner={a['owner']} data_tier={a['data_tier']} status={e['status']}")
EOF
resp=$(curl -s -m 10 -u "$CRED" \
  "$ATLAS_URL/api/atlas/v2/search/basic?typeName=$TYPE&excludeDeletedEntities=true")
echo "$resp" > /tmp/atlas_search.json
python3 - "$QUALIFIED_NAME" <<'EOF' || exit 1
import json, sys
d = json.load(open('/tmp/atlas_search.json'))
qn = sys.argv[1]
matches = [e for e in d.get('entities', []) if e['attributes'].get('qualifiedName') == qn]
assert matches, f"entity {qn} not found by search - Solr index out of sync?"
print(f"    search OK: {len(matches)} match(es), approximateCount={d.get('approximateCount')}")
EOF
echo "    OK"

echo ""
echo "ALL SMOKE TESTS PASSED - guid=$guid"
echo "Web UI: $ATLAS_URL  (admin/admin)"
