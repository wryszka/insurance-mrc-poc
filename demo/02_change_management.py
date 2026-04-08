# Databricks notebook source
# MAGIC %md
# MAGIC # Change Management — Updating the Pipeline
# MAGIC
# MAGIC **Demo scenario**: The real world isn't static. ACORD standards evolve, new policies arrive, and the system needs to adapt. This notebook shows two change scenarios:
# MAGIC
# MAGIC 1. **Scenario A**: The ACORD dictionary changes (new entity types, modified relationships)
# MAGIC 2. **Scenario B**: New MRC policy files are added to the volume
# MAGIC
# MAGIC ---

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scenario A: ACORD Dictionary Change
# MAGIC
# MAGIC **Situation**: Lloyd's introduces a new entity type **"Subjectivity"** — conditions that must be met before cover attaches. We also want to add a **"Territory"** entity and a **"COVERS_TERRITORY"** relationship.
# MAGIC
# MAGIC ### A1. Current state of the dictionary

# COMMAND ----------

import json

with open("/Workspace/Users/laurence.ryszka@databricks.com/insurance_mrc_poc/acord_dictionary.json") as f:
    acord = json.load(f)

print("Current entities:", list(acord["entities"].keys()))
print("Current relationships:", list(acord["relationships"].keys()))

# COMMAND ----------

# MAGIC %md
# MAGIC ### A2. Add new entity types and relationships
# MAGIC
# MAGIC We simply update the JSON dictionary — no code changes, no redeployment. The LLM reads the dictionary at extraction time.

# COMMAND ----------

# Add Subjectivity entity
acord["entities"]["Subjectivity"] = {
    "description": "A condition precedent that must be satisfied before coverage attaches",
    "attributes": ["subjectivity_id", "description", "deadline", "status"]
}

# Add Territory entity
acord["entities"]["Territory"] = {
    "description": "Geographic territory or jurisdiction covered by the policy",
    "attributes": ["territory_name", "includes", "excludes"]
}

# Add new relationships
acord["relationships"]["HAS_SUBJECTIVITY"] = {
    "source": "Policy",
    "target": "Subjectivity",
    "description": "A condition that must be met before the policy is fully bound"
}

acord["relationships"]["COVERS_TERRITORY"] = {
    "source": "Policy",
    "target": "Territory",
    "description": "Geographic territory covered by the policy"
}

print("Updated entities:", list(acord["entities"].keys()))
print("Updated relationships:", list(acord["relationships"].keys()))
print(f"\nNew: {len(acord['entities'])} entities, {len(acord['relationships'])} relationships")

# COMMAND ----------

# MAGIC %md
# MAGIC ### A3. Save and verify
# MAGIC
# MAGIC Write the updated dictionary back. The next extraction run will automatically use the new schema.

# COMMAND ----------

# Save updated dictionary
with open("/Workspace/Users/laurence.ryszka@databricks.com/insurance_mrc_poc/acord_dictionary.json", "w") as f:
    json.dump(acord, f, indent=2)

print("Dictionary updated. New entities will be extracted on next pipeline run.")

# COMMAND ----------

# MAGIC %md
# MAGIC ### A4. Re-extract a single document to show the new entities
# MAGIC
# MAGIC Let's re-run extraction on one policy to see the new entity types appear.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Parse the cyber policy (likely to have subjectivities and territory clauses)
# MAGIC WITH parsed AS (
# MAGIC   SELECT cast(ai_parse_document(content) AS STRING) AS doc_json
# MAGIC   FROM read_files('/Volumes/lr_serverless_aws_us_catalog/insurance_mrc_assistant/raw_policies/mrc_policy_004.pdf')
# MAGIC ),
# MAGIC doc_text AS (
# MAGIC   SELECT concat_ws('\n',
# MAGIC     collect_list(element.content)
# MAGIC   ) AS full_text
# MAGIC   FROM parsed
# MAGIC   LATERAL VIEW explode(
# MAGIC     from_json(doc_json, 'STRUCT<document:STRUCT<elements:ARRAY<STRUCT<type:STRING,content:STRING>>>>').document.elements
# MAGIC   ) t AS element
# MAGIC )
# MAGIC SELECT ai_query(
# MAGIC   'databricks-meta-llama-3-3-70b-instruct',
# MAGIC   concat(
# MAGIC     'Extract all entities and relationships from this insurance document. Include these entity types: Policy, Insured, Broker, Insurer, Limit, Deductible, Clause, Exclusion, Premium, Subjectivity, Territory.\n',
# MAGIC     'A Subjectivity is a condition that must be met before cover attaches (e.g. "Subject to satisfactory IT security audit").\n',
# MAGIC     'A Territory is the geographic scope of coverage.\n',
# MAGIC     'Return JSON: {"nodes": [...], "edges": [...]}\n\nDocument:\n',
# MAGIC     full_text
# MAGIC   )
# MAGIC ) AS extraction_with_new_entities
# MAGIC FROM doc_text

# COMMAND ----------

# MAGIC %md
# MAGIC ### Key Point — Scenario A
# MAGIC
# MAGIC > **Changing the extraction schema requires zero code changes.** Update the JSON dictionary, re-run the pipeline. The LLM adapts automatically.
# MAGIC >
# MAGIC > In production, this would be:
# MAGIC > 1. Update `acord_dictionary.json` in the UC Volume or Git repo
# MAGIC > 2. Trigger the extraction job (scheduled or on-demand)
# MAGIC > 3. New entities appear in the graph tables
# MAGIC > 4. The SQL Agent can immediately query them — no retraining needed

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Scenario B: New Policy Files Added
# MAGIC
# MAGIC **Situation**: A new MRC arrives — a Professional Indemnity policy for a fintech company. We need to add it to the system.
# MAGIC
# MAGIC ### B1. Current state

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Current files in the volume
# MAGIC LIST '/Volumes/lr_serverless_aws_us_catalog/insurance_mrc_assistant/raw_policies/'

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Current graph size
# MAGIC SELECT 'Nodes' AS metric, COUNT(*) AS count FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes
# MAGIC UNION ALL
# MAGIC SELECT 'Edges', COUNT(*) FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_edges

# COMMAND ----------

# MAGIC %md
# MAGIC ### B2. Upload a new policy
# MAGIC
# MAGIC In production, files would arrive via an automated ingestion pipeline. For demo purposes, we simulate a new MRC arriving.

# COMMAND ----------

from databricks.sdk import WorkspaceClient
w = WorkspaceClient()

# Check: if we had a 6th policy, we'd upload it like this:
# with open("mrc_policy_006.pdf", "rb") as f:
#     w.files.upload("/Volumes/lr_serverless_aws_us_catalog/insurance_mrc_assistant/raw_policies/mrc_policy_006.pdf", f, overwrite=True)

# For demo, let's show what incremental extraction looks like.
# We'll re-extract just ONE file and INSERT (not replace) into the graph.
print("In production:")
print("  1. New PDF arrives in UC Volume (via upload, pipeline, or sync)")
print("  2. Extraction job detects new/changed files")
print("  3. Only new files are parsed and extracted (incremental)")
print("  4. New nodes/edges are INSERTed into the graph tables")
print("  5. Knowledge Assistant auto-syncs from the volume")

# COMMAND ----------

# MAGIC %md
# MAGIC ### B3. Incremental extraction pattern
# MAGIC
# MAGIC The extraction pipeline can be made incremental by tracking which files have been processed.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Create a processing log table (if not exists)
# MAGIC CREATE TABLE IF NOT EXISTS lr_serverless_aws_us_catalog.insurance_mrc_assistant.processing_log (
# MAGIC   file_name STRING,
# MAGIC   processed_at TIMESTAMP,
# MAGIC   node_count INT,
# MAGIC   edge_count INT,
# MAGIC   status STRING
# MAGIC )
# MAGIC USING DELTA

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Simulate logging a processed file
# MAGIC INSERT INTO lr_serverless_aws_us_catalog.insurance_mrc_assistant.processing_log VALUES
# MAGIC ('mrc_policy_001.pdf', current_timestamp(), 20, 19, 'SUCCESS'),
# MAGIC ('mrc_policy_002.pdf', current_timestamp(), 18, 17, 'SUCCESS'),
# MAGIC ('mrc_policy_003.pdf', current_timestamp(), 21, 20, 'SUCCESS'),
# MAGIC ('mrc_policy_004.pdf', current_timestamp(), 23, 22, 'SUCCESS'),
# MAGIC ('mrc_policy_005.pdf', current_timestamp(), 23, 22, 'SUCCESS')

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Find new files that haven't been processed yet
# MAGIC -- This query drives incremental extraction
# MAGIC SELECT f.name AS new_file
# MAGIC FROM list_volume_files('/Volumes/lr_serverless_aws_us_catalog/insurance_mrc_assistant/raw_policies/') f
# MAGIC LEFT JOIN lr_serverless_aws_us_catalog.insurance_mrc_assistant.processing_log p
# MAGIC   ON f.name = p.file_name
# MAGIC WHERE p.file_name IS NULL

# COMMAND ----------

# MAGIC %md
# MAGIC ### B4. Knowledge Assistant auto-sync
# MAGIC
# MAGIC The Knowledge Assistant monitors the UC Volume. When new files appear, it automatically re-indexes.

# COMMAND ----------

from databricks.sdk import WorkspaceClient
w = WorkspaceClient()

# Trigger a sync of the knowledge sources
ka_name = "knowledge-assistants/04bfe483-92eb-42c9-970c-d796f99028a1"
w.knowledge_assistants.sync_knowledge_sources(name=ka_name)
print("Knowledge Assistant sync triggered — new documents will be searchable within minutes")

# Check current state
ka = w.knowledge_assistants.get_knowledge_assistant(ka_name)
print(f"State: {ka.state}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Key Point — Scenario B
# MAGIC
# MAGIC > **Adding new documents is drop-and-go.** Upload to the volume, trigger extraction, done.
# MAGIC >
# MAGIC > | Component | What happens when files change |
# MAGIC > |-----------|-------------------------------|
# MAGIC > | UC Volume | New file appears automatically |
# MAGIC > | ai_parse_document | Parses new file on demand |
# MAGIC > | ai_query (LLM) | Extracts graph from new file |
# MAGIC > | Delta Tables | New rows inserted (append) |
# MAGIC > | Knowledge Assistant | Auto-syncs from volume |
# MAGIC > | SQL Agent | Queries new data immediately |
# MAGIC > | Supervisor | Routes to updated tools — no change needed |
# MAGIC >
# MAGIC > **No retraining. No redeployment. No downtime.**
