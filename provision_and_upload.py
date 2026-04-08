"""
Step 2: Provision Unity Catalog schema, volume, upload PDFs, and create Delta tables.
Uses databricks-sdk WorkspaceClient with profile authentication.
"""

import os
import glob
import json
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.catalog import VolumeType, SchemasAPI

PROFILE = "fevm-lr-serverless-aws-us"
CATALOG = "lr_serverless_aws_us_catalog"
SCHEMA = "insurance_poc"
VOLUME = "raw_policies"
FULL_SCHEMA = f"{CATALOG}.{SCHEMA}"
FULL_VOLUME = f"{CATALOG}.{SCHEMA}.{VOLUME}"
VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def main():
    w = WorkspaceClient(profile=PROFILE)
    print(f"Connected as: {w.current_user.me().user_name}")

    # ── 1. Create Schema ────────────────────────────────────────────────────
    print(f"\n[1/5] Creating schema: {FULL_SCHEMA}")
    try:
        w.schemas.create(name=SCHEMA, catalog_name=CATALOG, comment="Insurance MRC POC - graph and vector pipeline")
        print(f"  Created schema: {FULL_SCHEMA}")
    except Exception as e:
        if "SCHEMA_ALREADY_EXISTS" in str(e) or "already exists" in str(e).lower():
            print(f"  Schema already exists: {FULL_SCHEMA}")
        else:
            raise

    # ── 2. Create Volume ────────────────────────────────────────────────────
    print(f"\n[2/5] Creating volume: {FULL_VOLUME}")
    try:
        w.volumes.create(
            catalog_name=CATALOG,
            schema_name=SCHEMA,
            name=VOLUME,
            volume_type=VolumeType.MANAGED,
            comment="Raw MRC policy PDFs for insurance POC",
        )
        print(f"  Created volume: {FULL_VOLUME}")
    except Exception as e:
        if "already exists" in str(e).lower():
            print(f"  Volume already exists: {FULL_VOLUME}")
        else:
            raise

    # ── 3. Upload PDFs ──────────────────────────────────────────────────────
    print(f"\n[3/5] Uploading PDFs to {VOLUME_PATH}")
    pdf_files = sorted(glob.glob(os.path.join(OUTPUT_DIR, "*.pdf")))
    if not pdf_files:
        raise FileNotFoundError(f"No PDFs found in {OUTPUT_DIR}")

    for pdf_path in pdf_files:
        filename = os.path.basename(pdf_path)
        remote_path = f"{VOLUME_PATH}/{filename}"
        with open(pdf_path, "rb") as f:
            w.files.upload(remote_path, f, overwrite=True)
        size_kb = os.path.getsize(pdf_path) / 1024
        print(f"  Uploaded: {filename} ({size_kb:.1f} KB)")

    # ── 4. Verify uploads ───────────────────────────────────────────────────
    print(f"\n[4/5] Verifying uploads in {VOLUME_PATH}")
    uploaded = list(w.files.list_directory_contents(VOLUME_PATH))
    print(f"  Files in volume: {len(uploaded)}")
    for f in uploaded:
        print(f"    - {f.name} ({f.file_size} bytes)")

    # ── 5. Create Delta Tables ──────────────────────────────────────────────
    print(f"\n[5/5] Creating Delta tables")

    create_nodes_sql = f"""
    CREATE TABLE IF NOT EXISTS {FULL_SCHEMA}.graph_nodes (
        node_id STRING NOT NULL COMMENT 'Unique identifier for the node',
        label STRING NOT NULL COMMENT 'Entity type: Policy, Insured, Broker, Insurer, Limit, Deductible, Clause, Exclusion, Premium',
        properties STRING COMMENT 'JSON string of entity attributes'
    )
    USING DELTA
    COMMENT 'Graph nodes extracted from MRC policy documents'
    """

    create_edges_sql = f"""
    CREATE TABLE IF NOT EXISTS {FULL_SCHEMA}.graph_edges (
        source_id STRING NOT NULL COMMENT 'Source node ID',
        target_id STRING NOT NULL COMMENT 'Target node ID',
        relationship_type STRING NOT NULL COMMENT 'Relationship: PLACED_BY, ISSUED_TO, UNDERWRITTEN_BY, HAS_LIMIT, HAS_DEDUCTIBLE, CONTAINS_CLAUSE, EXCLUDES, HAS_PREMIUM'
    )
    USING DELTA
    COMMENT 'Graph edges representing relationships between MRC policy entities'
    """

    # Execute via statement execution API (serverless SQL)
    warehouse_id = _get_sql_warehouse(w)
    print(f"  Using SQL warehouse: {warehouse_id}")

    _execute_sql(w, warehouse_id, create_nodes_sql)
    print(f"  Created table: {FULL_SCHEMA}.graph_nodes")

    _execute_sql(w, warehouse_id, create_edges_sql)
    print(f"  Created table: {FULL_SCHEMA}.graph_edges")

    # Verify tables
    for table_name in ["graph_nodes", "graph_edges"]:
        result = _execute_sql(w, warehouse_id, f"DESCRIBE TABLE {FULL_SCHEMA}.{table_name}")
        cols = [row[0] for row in result if row[0] and not row[0].startswith("#")]
        print(f"  Verified {table_name} columns: {cols}")

    print("\n=== Step 2 Complete ===")
    print(f"  Schema:  {FULL_SCHEMA}")
    print(f"  Volume:  {FULL_VOLUME} ({len(pdf_files)} PDFs)")
    print(f"  Tables:  {FULL_SCHEMA}.graph_nodes")
    print(f"           {FULL_SCHEMA}.graph_edges")


def _get_sql_warehouse(w):
    """Find a running or startable SQL warehouse."""
    warehouses = list(w.warehouses.list())
    # Prefer serverless warehouses
    for wh in warehouses:
        if wh.enable_serverless_compute:
            return wh.id
    # Fall back to any warehouse
    if warehouses:
        return warehouses[0].id
    raise RuntimeError("No SQL warehouse found. Please create one in the workspace.")


def _execute_sql(w, warehouse_id, sql):
    """Execute SQL and return results."""
    from databricks.sdk.service.sql import StatementState
    import time

    resp = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=sql.strip(),
        wait_timeout="50s",
    )

    # Poll if needed
    while resp.status and resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(1)
        resp = w.statement_execution.get_statement(resp.statement_id)

    if resp.status and resp.status.state == StatementState.FAILED:
        raise RuntimeError(f"SQL failed: {resp.status.error}")

    if resp.result and resp.result.data_array:
        return resp.result.data_array
    return []


if __name__ == "__main__":
    main()
