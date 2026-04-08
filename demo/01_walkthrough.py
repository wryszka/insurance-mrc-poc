# Databricks notebook source
# MAGIC %md
# MAGIC # Insurance MRC Pipeline — End-to-End Walkthrough
# MAGIC
# MAGIC **Demo scenario**: A Lloyd's syndicate wants to extract structured knowledge from Market Reform Contracts and build an AI assistant that can answer questions about policy terms, limits, exclusions, and relationships.
# MAGIC
# MAGIC This notebook walks through every stage of the pipeline — from raw PDF to multi-agent assistant.
# MAGIC
# MAGIC > **Disclaimer**: This is not a Databricks product. The data is synthetic. Regulatory templates, actuarial logic, and AI prompts are illustrative — not for production use or actual regulatory submissions. [Source code on GitHub](https://github.com/wryszka/insurance-mrc-poc).
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Architecture
# MAGIC ```
# MAGIC ┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
# MAGIC │  MRC PDFs    │────▶│  ai_parse_document│────▶│  ai_query (LLM)  │
# MAGIC │  (UC Volume) │     │  (text extraction)│     │  (graph extract) │
# MAGIC └──────────────┘     └──────────────────┘     └────────┬─────────┘
# MAGIC                                                         │
# MAGIC                           ┌─────────────────────────────┤
# MAGIC                           ▼                             ▼
# MAGIC                  ┌─────────────────┐          ┌─────────────────┐
# MAGIC                  │  graph_nodes     │          │  graph_edges     │
# MAGIC                  │  (Delta Table)   │          │  (Delta Table)   │
# MAGIC                  └────────┬────────┘          └────────┬────────┘
# MAGIC                           │                             │
# MAGIC                           ▼                             ▼
# MAGIC                  ┌──────────────────────────────────────────────┐
# MAGIC                  │         SQL Sub-Agent (Tool B)               │
# MAGIC                  │  "Find limits, relationships, aggregations"  │
# MAGIC                  └──────────────────┬───────────────────────────┘
# MAGIC                                     │
# MAGIC   ┌──────────────────┐              │
# MAGIC   │  Knowledge Asst   │              │
# MAGIC   │  (Vector Search)  │◀─── Tool A ──┤
# MAGIC   │  raw PDFs indexed  │              │
# MAGIC   └──────────────────┘              │
# MAGIC                                     ▼
# MAGIC                           ┌──────────────────┐
# MAGIC                           │   Supervisor      │
# MAGIC                           │   Agent (Claude)  │
# MAGIC                           └──────────────────┘
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. The Source Material — Lloyd's MRC v3 PDFs
# MAGIC
# MAGIC We have 5 Market Reform Contracts sitting in a Unity Catalog Volume. These are real-format documents with structured headings: Risk Details, Insured, Broker, Limits, Exclusions.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- What's in our document store?
# MAGIC LIST '/Volumes/lr_serverless_aws_us_catalog/insurance_mrc_assistant/raw_policies/'

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. The ACORD Dictionary — Our Extraction Schema
# MAGIC
# MAGIC Before we extract anything, we define **what** to extract. The ACORD dictionary specifies:
# MAGIC - **Entities**: Insured, Broker, Insurer, Policy, Limit, Deductible, Clause, Exclusion, Premium
# MAGIC - **Relationships**: PLACED_BY, ISSUED_TO, UNDERWRITTEN_BY, HAS_LIMIT, EXCLUDES, etc.
# MAGIC
# MAGIC This is the "ontology" that turns unstructured PDFs into a knowledge graph.

# COMMAND ----------

import json

acord = json.loads(open("/Workspace/Users/laurence.ryszka@databricks.com/insurance_mrc_poc/acord_dictionary.json").read())
print("Entity types:", list(acord["entities"].keys()))
print("Relationship types:", list(acord["relationships"].keys()))

# Show one entity definition
print("\nExample — 'Limit' entity:")
print(json.dumps(acord["entities"]["Limit"], indent=2))

# Show one relationship
print("\nExample — 'EXCLUDES' relationship:")
print(json.dumps(acord["relationships"]["EXCLUDES"], indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Step 1 — Document Parsing with `ai_parse_document()`
# MAGIC
# MAGIC Databricks' built-in `ai_parse_document()` extracts text, layout, and structure from PDFs. No external OCR service needed — it runs natively on the platform.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Parse the first MRC and see what we get
# MAGIC SELECT
# MAGIC   cast(ai_parse_document(content) AS STRING) AS parsed_json
# MAGIC FROM read_files('/Volumes/lr_serverless_aws_us_catalog/insurance_mrc_assistant/raw_policies/mrc_policy_001.pdf')

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Extract just the text elements for readability
# MAGIC WITH parsed AS (
# MAGIC   SELECT cast(ai_parse_document(content) AS STRING) AS doc_json
# MAGIC   FROM read_files('/Volumes/lr_serverless_aws_us_catalog/insurance_mrc_assistant/raw_policies/mrc_policy_001.pdf')
# MAGIC )
# MAGIC SELECT
# MAGIC   element.type,
# MAGIC   element.content
# MAGIC FROM parsed
# MAGIC LATERAL VIEW explode(from_json(doc_json, 'STRUCT<document:STRUCT<elements:ARRAY<STRUCT<type:STRING,content:STRING>>>>').document.elements) t AS element

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Step 2 — Graph Extraction with `ai_query()` + LLM
# MAGIC
# MAGIC Now we feed the parsed text to **Llama 3.3 70B** along with our ACORD dictionary rules. The LLM extracts entities and relationships as structured JSON.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Ask the LLM to extract graph nodes from a parsed document
# MAGIC SELECT ai_query(
# MAGIC   'databricks-meta-llama-3-3-70b-instruct',
# MAGIC   'Extract all entity nodes from this insurance document. Return JSON with a "nodes" array where each node has node_id, label, and properties.
# MAGIC
# MAGIC Document: Unique Market Reference (UMR): B0999ABC123456. Policy Number: MRC-2025-LL-001. Class of Business: Property Damage. Insured: Meridian Global Industries PLC. Broker: Aon UK Limited (code 0780). Limit: GBP 50,000,000 any one occurrence.
# MAGIC
# MAGIC Entity types to extract: Policy, Insured, Broker, Limit.
# MAGIC Return ONLY valid JSON.'
# MAGIC ) AS extracted_graph

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. The Knowledge Graph — What We Built
# MAGIC
# MAGIC After running the full extraction pipeline across all 5 MRCs, we have a graph stored in two Delta tables.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Node summary
# MAGIC SELECT label, COUNT(*) as count
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes
# MAGIC GROUP BY label
# MAGIC ORDER BY count DESC

# COMMAND ----------

# MAGIC %sql
# MAGIC -- All policies at a glance
# MAGIC SELECT
# MAGIC   node_id,
# MAGIC   properties:policy_number AS policy_number,
# MAGIC   properties:class_of_business AS class_of_business,
# MAGIC   properties:inception_date AS inception,
# MAGIC   properties:expiry_date AS expiry
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes
# MAGIC WHERE label = 'Policy'

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Relationship summary
# MAGIC SELECT relationship_type, COUNT(*) as count
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_edges
# MAGIC GROUP BY relationship_type
# MAGIC ORDER BY count DESC

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Graph traversal: Which broker placed each policy?
# MAGIC SELECT
# MAGIC   p.properties:policy_number AS policy,
# MAGIC   b.properties:name AS broker,
# MAGIC   b.properties:lloyds_broker_code AS broker_code
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_edges e
# MAGIC JOIN lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes p ON e.source_id = p.node_id
# MAGIC JOIN lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes b ON e.target_id = b.node_id
# MAGIC WHERE e.relationship_type = 'PLACED_BY'

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Graph traversal: All limits by policy
# MAGIC SELECT
# MAGIC   p.properties:policy_number AS policy,
# MAGIC   p.properties:class_of_business AS class,
# MAGIC   l.properties:type AS limit_type,
# MAGIC   l.properties:amount AS amount,
# MAGIC   l.properties:basis AS basis
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_edges e
# MAGIC JOIN lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes p ON e.source_id = p.node_id
# MAGIC JOIN lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes l ON e.target_id = l.node_id
# MAGIC WHERE e.relationship_type = 'HAS_LIMIT'
# MAGIC ORDER BY p.properties:policy_number

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. The Knowledge Assistant — Vector Search Over Raw Documents
# MAGIC
# MAGIC In parallel to the knowledge graph, we created a **Knowledge Assistant** that indexes the raw PDFs using vector search. This handles questions that need the actual wording — clause text, exclusion details, coverage descriptions.

# COMMAND ----------

from databricks.sdk import WorkspaceClient
w = WorkspaceClient()

# Query the Knowledge Assistant directly
resp = w.serving_endpoints.query(
    name="ka-04bfe483-endpoint",
    input={"messages": [{"role": "user", "content": "What exclusions apply to the cyber liability policy?"}]},
)
print(resp.choices[0].message.content if hasattr(resp, 'choices') else resp)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. The Multi-Agent Supervisor — Bringing It All Together
# MAGIC
# MAGIC The **Supervisor Agent** (powered by Claude) receives a question and routes it:
# MAGIC - **Text/semantic questions** → Knowledge Assistant (vector search over PDFs)
# MAGIC - **Structured/relationship questions** → SQL Agent (queries the graph tables)
# MAGIC - **Complex questions** → Both tools, then synthesises
# MAGIC
# MAGIC The user doesn't need to know which tool to use — the supervisor decides.

# COMMAND ----------

# Query the supervisor
resp = w.serving_endpoints.query(
    name="agents_lr_serverless_aws_us_catalog-insurance_mrc_assistant-insurance_sup",
    input={"messages": [{"role": "user", "content": "Which policy has the highest aggregate limit, and what exclusions apply to it?"}]},
)
print(resp.choices[0].message.content if hasattr(resp, 'choices') else resp)

# COMMAND ----------

# Another query — pure graph traversal
resp = w.serving_endpoints.query(
    name="agents_lr_serverless_aws_us_catalog-insurance_mrc_assistant-insurance_sup",
    input={"messages": [{"role": "user", "content": "List all syndicates that underwrite more than one policy"}]},
)
print(resp.choices[0].message.content if hasattr(resp, 'choices') else resp)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. The App — Self-Service for Underwriters
# MAGIC
# MAGIC Finally, we wrapped the agent in a **Databricks App** — a simple chat interface that any underwriter can use without touching SQL or code.
# MAGIC
# MAGIC **App URL**: https://insurance-mrc-assistant-7474659673789953.aws.databricksapps.com
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Key Takeaways
# MAGIC
# MAGIC | Layer | Databricks Service | What it does |
# MAGIC |-------|-------------------|-------------|
# MAGIC | Storage | Unity Catalog Volumes | Stores raw MRC PDFs |
# MAGIC | Parsing | `ai_parse_document()` | Extracts text from PDFs natively |
# MAGIC | Extraction | `ai_query()` + Llama 3.3 70B | Converts text to graph (nodes + edges) |
# MAGIC | Graph | Delta Tables | Stores knowledge graph in SQL-queryable format |
# MAGIC | Vector Search | Knowledge Assistant | Semantic search over raw documents |
# MAGIC | SQL Agent | Mosaic AI Agent Framework | Natural language to SQL over graph |
# MAGIC | Supervisor | Claude Sonnet 4.6 | Routes queries to right tool, synthesises |
# MAGIC | App | Databricks Apps | Self-service chat UI |
# MAGIC
# MAGIC **No external services. No graph databases. Everything runs on Databricks.**
