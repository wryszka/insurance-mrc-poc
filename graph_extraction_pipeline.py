"""
Step 3: Graph Extraction Pipeline
- Reads PDFs from UC Volume via ai_parse_document()
- Extracts nodes and edges using ai_query() with Llama 3.3 70B
- Inserts results into graph_nodes and graph_edges Delta tables
"""

import os
import json
import time
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

PROFILE = "fevm-lr-serverless-aws-us"
CATALOG = "lr_serverless_aws_us_catalog"
SCHEMA = "insurance_mrc_assistant"
FULL_SCHEMA = f"{CATALOG}.{SCHEMA}"
VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/raw_policies"
LLM_ENDPOINT = "databricks-meta-llama-3-3-70b-instruct"

ACORD_DICT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "acord_dictionary.json")


def main():
    w = WorkspaceClient(profile=PROFILE)
    print(f"Connected as: {w.current_user.me().user_name}")

    with open(ACORD_DICT_PATH) as f:
        acord_dict = json.load(f)

    warehouse_id = _get_sql_warehouse(w)
    print(f"Using SQL warehouse: {warehouse_id}")

    # ── 1. List PDFs in volume ──────────────────────────────────────────────
    files = list(w.files.list_directory_contents(VOLUME_PATH))
    pdf_files = [f.name for f in files if f.name.endswith(".pdf")]
    print(f"\nFound {len(pdf_files)} PDFs to process")

    # ── 2. Clear existing data for idempotency ──────────────────────────────
    print("\nClearing existing graph data...")
    _execute_sql(w, warehouse_id, f"TRUNCATE TABLE {FULL_SCHEMA}.graph_nodes")
    _execute_sql(w, warehouse_id, f"TRUNCATE TABLE {FULL_SCHEMA}.graph_edges")

    # ── 3. Process each PDF ─────────────────────────────────────────────────
    total_nodes = 0
    total_edges = 0

    for pdf_name in pdf_files:
        print(f"\n{'='*60}")
        print(f"Processing: {pdf_name}")

        # 3a. Parse document using ai_parse_document() via read_files()
        print(f"  Parsing document with ai_parse_document()...")
        parse_sql = f"""
        SELECT cast(ai_parse_document(content) AS STRING) AS parsed_json
        FROM read_files('{VOLUME_PATH}/{pdf_name}')
        """
        result = _execute_sql(w, warehouse_id, parse_sql)
        if not result or not result[0][0]:
            print(f"  WARNING: No text extracted from {pdf_name}, skipping")
            continue

        # Extract text content from the parsed document JSON
        parsed_json = json.loads(result[0][0])
        elements = parsed_json.get("document", {}).get("elements", [])
        parsed_text = "\n".join(el.get("content", "") for el in elements if el.get("content"))
        preview = parsed_text[:200].replace("\n", " ")
        print(f"  Extracted {len(parsed_text)} chars from {len(elements)} elements: {preview}...")

        # 3b. Build extraction prompt with ACORD dictionary rules
        entity_defs = json.dumps(acord_dict["entities"], indent=2)
        relationship_defs = json.dumps(acord_dict["relationships"], indent=2)

        extraction_prompt = _build_extraction_prompt(entity_defs, relationship_defs, parsed_text)

        # 3c. Call ai_query() with Llama 3.3 70B
        print(f"  Extracting graph with ai_query({LLM_ENDPOINT})...")

        # Escape single quotes for SQL
        escaped_prompt = extraction_prompt.replace("'", "''")

        extract_sql = f"""
        SELECT ai_query(
            '{LLM_ENDPOINT}',
            '{escaped_prompt}'
        ) AS extraction_result
        """

        result = _execute_sql(w, warehouse_id, extract_sql)
        if not result or not result[0][0]:
            print(f"  WARNING: No extraction result for {pdf_name}, skipping")
            continue

        raw_response = result[0][0]
        print(f"  Raw LLM response: {len(raw_response)} chars")

        # 3d. Parse the JSON from the LLM response
        graph_data = _parse_llm_json(raw_response)
        if not graph_data:
            print(f"  WARNING: Could not parse JSON from LLM response for {pdf_name}")
            print(f"  Response preview: {raw_response[:500]}")
            continue

        nodes = graph_data.get("nodes", [])
        edges = graph_data.get("edges", [])
        print(f"  Extracted: {len(nodes)} nodes, {len(edges)} edges")

        # 3e. Insert nodes
        if nodes:
            _insert_nodes(w, warehouse_id, nodes)
            total_nodes += len(nodes)

        # 3f. Insert edges
        if edges:
            _insert_edges(w, warehouse_id, edges)
            total_edges += len(edges)

        print(f"  Done: {pdf_name}")

    # ── 4. Verify results ───────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("VERIFICATION")

    node_count = _execute_sql(w, warehouse_id, f"SELECT COUNT(*) FROM {FULL_SCHEMA}.graph_nodes")
    edge_count = _execute_sql(w, warehouse_id, f"SELECT COUNT(*) FROM {FULL_SCHEMA}.graph_edges")
    print(f"  Total nodes in table: {node_count[0][0]}")
    print(f"  Total edges in table: {edge_count[0][0]}")

    # Sample data
    print("\n  Sample nodes:")
    sample_nodes = _execute_sql(w, warehouse_id,
        f"SELECT node_id, label, LEFT(properties, 80) FROM {FULL_SCHEMA}.graph_nodes LIMIT 5")
    for row in sample_nodes:
        print(f"    {row[0]} | {row[1]} | {row[2]}")

    print("\n  Sample edges:")
    sample_edges = _execute_sql(w, warehouse_id,
        f"SELECT source_id, target_id, relationship_type FROM {FULL_SCHEMA}.graph_edges LIMIT 5")
    for row in sample_edges:
        print(f"    {row[0]} -> {row[1]} [{row[2]}]")

    # Label distribution
    print("\n  Node label distribution:")
    dist = _execute_sql(w, warehouse_id,
        f"SELECT label, COUNT(*) as cnt FROM {FULL_SCHEMA}.graph_nodes GROUP BY label ORDER BY cnt DESC")
    for row in dist:
        print(f"    {row[0]}: {row[1]}")

    print(f"\n=== Step 3 Complete: {total_nodes} nodes, {total_edges} edges extracted ===")


def _build_extraction_prompt(entity_defs, relationship_defs, document_text):
    """Build the extraction prompt using ACORD dictionary rules."""
    return f"""You are an insurance document graph extractor. Extract structured entities and relationships from the following Lloyd's Market Reform Contract.

ENTITY DEFINITIONS (extract these types):
{entity_defs}

RELATIONSHIP DEFINITIONS (extract these connections):
{relationship_defs}

RULES:
1. Each node needs a unique node_id (use format: <entity_type>_<short_identifier>, e.g. policy_MRC2025LL001, insured_meridian, broker_aon)
2. Each node needs a label (the entity type name) and a properties JSON object with all relevant attributes
3. Each edge needs source_id, target_id, and relationship_type matching the definitions above
4. Extract ALL entities and relationships you can find in the document
5. Return ONLY valid JSON with no explanation text

DOCUMENT TEXT:
{document_text}

Return JSON in exactly this format:
{{"nodes": [{{"node_id": "...", "label": "...", "properties": {{...}}}}, ...], "edges": [{{"source_id": "...", "target_id": "...", "relationship_type": "..."}}, ...]}}"""


def _parse_llm_json(raw_response):
    """Extract JSON from LLM response, handling markdown code blocks."""
    text = raw_response.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            cleaned = part.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                continue

    # Try finding JSON object boundaries
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    return None


def _insert_nodes(w, warehouse_id, nodes):
    """Insert nodes into graph_nodes table."""
    values = []
    for node in nodes:
        nid = node.get("node_id", "").replace("'", "''")
        label = node.get("label", "").replace("'", "''")
        props = json.dumps(node.get("properties", {})).replace("'", "''")
        values.append(f"('{nid}', '{label}', '{props}')")

    # Batch insert in chunks of 50
    for i in range(0, len(values), 50):
        batch = values[i:i+50]
        sql = f"INSERT INTO {FULL_SCHEMA}.graph_nodes (node_id, label, properties) VALUES {', '.join(batch)}"
        _execute_sql(w, warehouse_id, sql)


def _insert_edges(w, warehouse_id, edges):
    """Insert edges into graph_edges table."""
    values = []
    for edge in edges:
        src = edge.get("source_id", "").replace("'", "''")
        tgt = edge.get("target_id", "").replace("'", "''")
        rel = edge.get("relationship_type", "").replace("'", "''")
        values.append(f"('{src}', '{tgt}', '{rel}')")

    for i in range(0, len(values), 50):
        batch = values[i:i+50]
        sql = f"INSERT INTO {FULL_SCHEMA}.graph_edges (source_id, target_id, relationship_type) VALUES {', '.join(batch)}"
        _execute_sql(w, warehouse_id, sql)


def _get_sql_warehouse(w):
    """Find a serverless SQL warehouse."""
    warehouses = list(w.warehouses.list())
    for wh in warehouses:
        if wh.enable_serverless_compute:
            return wh.id
    if warehouses:
        return warehouses[0].id
    raise RuntimeError("No SQL warehouse found")


def _execute_sql(w, warehouse_id, sql):
    """Execute SQL statement and return results."""
    resp = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=sql.strip(),
        wait_timeout="50s",
    )

    while resp.status and resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(2)
        resp = w.statement_execution.get_statement(resp.statement_id)

    if resp.status and resp.status.state == StatementState.FAILED:
        raise RuntimeError(f"SQL failed: {resp.status.error}")

    if resp.result and resp.result.data_array:
        return resp.result.data_array
    return []


if __name__ == "__main__":
    main()
