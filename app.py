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
