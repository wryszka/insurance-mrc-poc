# Databricks notebook source
# MAGIC %md
# MAGIC # Performance & Architecture — Latency Analysis
# MAGIC
# MAGIC **Question**: If this was a real production system, what would the answer latency be?
# MAGIC
# MAGIC This notebook breaks down the latency of each step, compares the current architecture with a production-optimised version, and contrasts with a traditional graph database approach.
# MAGIC
# MAGIC > **Disclaimer**: This is not a Databricks product. Data is synthetic. Provided as-is for demonstration purposes.
# MAGIC
# MAGIC ---

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Current Architecture — Measured Latency
# MAGIC
# MAGIC When a user asks a question, the supervisor agent makes **multiple sequential LLM calls**. Let's measure each step.

# COMMAND ----------

import time
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
wh_id = [wh.id for wh in w.warehouses.list() if wh.enable_serverless_compute][0]

def timed_sql(sql):
    from databricks.sdk.service.sql import StatementState
    start = time.time()
    resp = w.statement_execution.execute_statement(warehouse_id=wh_id, statement=sql.strip(), wait_timeout="50s")
    while resp.status and resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(0.5)
        resp = w.statement_execution.get_statement(resp.statement_id)
    elapsed = time.time() - start
    result = resp.result.data_array if resp.result and resp.result.data_array else []
    return elapsed, result

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 1: Routing — LLM decides which tool to use

# COMMAND ----------

routing_prompt = """You are a router. Given a user question, return JSON: {"tools": ["A"], "query": "..."} for text questions or {"tools": ["B"], "query": "..."} for data questions.
User: What are the total limits across all cyber liability policies?
Return ONLY JSON."""

t, r = timed_sql(f"SELECT ai_query('databricks-claude-sonnet-4-6', '{routing_prompt.replace(chr(39), chr(39)+chr(39))}')")
print(f"Routing latency: {t:.1f}s")
print(f"Response: {r[0][0][:200] if r else 'none'}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 2a: Knowledge Assistant — Vector search + LLM synthesis

# COMMAND ----------

start = time.time()
ka_resp = w.api_client.do(
    "POST",
    "/serving-endpoints/ka-04bfe483-endpoint/invocations",
    body={"input": [{"role": "user", "content": "What exclusions apply to the cyber liability policy?"}]},
)
ka_time = time.time() - start
print(f"Knowledge Assistant latency: {ka_time:.1f}s")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 2b: SQL Agent — LLM generates SQL, warehouse executes

# COMMAND ----------

# Step 2b-i: LLM generates SQL
gen_prompt = """Generate Databricks SQL: Find all limits for cyber policies.
Tables: lr_serverless_aws_us_catalog.insurance_poc.graph_nodes (node_id, label, properties), lr_serverless_aws_us_catalog.insurance_poc.graph_edges (source_id, target_id, relationship_type). Use properties:field for JSON. Return ONLY SQL."""

t_gen, r_gen = timed_sql(f"SELECT ai_query('databricks-claude-sonnet-4-6', '{gen_prompt.replace(chr(39), chr(39)+chr(39))}')")
print(f"SQL generation latency: {t_gen:.1f}s")

# Step 2b-ii: Execute the generated SQL
t_exec, r_exec = timed_sql("""
SELECT p.properties:policy_number AS policy, l.properties:amount AS amount, l.properties:currency AS currency
FROM lr_serverless_aws_us_catalog.insurance_poc.graph_edges e
JOIN lr_serverless_aws_us_catalog.insurance_poc.graph_nodes p ON e.source_id = p.node_id
JOIN lr_serverless_aws_us_catalog.insurance_poc.graph_nodes l ON e.target_id = l.node_id
WHERE e.relationship_type = 'HAS_LIMIT' AND p.properties:class_of_business LIKE '%Cyber%'
""")
print(f"SQL execution latency: {t_exec:.1f}s")
print(f"Results: {r_exec}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 3: Synthesis — LLM combines results

# COMMAND ----------

synth_prompt = "Summarise: Cyber policy MRC-2025-LL-004 has limits of USD 25M combined, USD 5M ransomware, USD 2.5M regulatory, USD 1M crisis comms. Total: USD 33.5M. Cite source as SQL Agent."
t_synth, _ = timed_sql(f"SELECT ai_query('databricks-claude-sonnet-4-6', '{synth_prompt.replace(chr(39), chr(39)+chr(39))}')")
print(f"Synthesis latency: {t_synth:.1f}s")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Summary — Current Architecture

# COMMAND ----------

total = t + ka_time + t_gen + t_exec + t_synth

print("=" * 60)
print("CURRENT ARCHITECTURE — LATENCY BREAKDOWN")
print("=" * 60)
print(f"  1. Routing (Claude)          : {t:.1f}s")
print(f"  2a. Knowledge Assistant       : {ka_time:.1f}s")
print(f"  2b. SQL generation (Claude)   : {t_gen:.1f}s")
print(f"  2b. SQL execution (warehouse) : {t_exec:.1f}s")
print(f"  3. Synthesis (Claude)         : {t_synth:.1f}s")
print(f"  {'─' * 40}")
print(f"  TOTAL (sequential)            : {total:.1f}s")
print(f"  TOTAL (parallel 2a+2b)        : {t + max(ka_time, t_gen + t_exec) + t_synth:.1f}s")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Production-Optimised Architecture — Projected Latency
# MAGIC
# MAGIC | Optimisation | Current | Optimised | Saving |
# MAGIC |-------------|---------|-----------|--------|
# MAGIC | **Parallel tool calls** | Sequential (2a then 2b) | Concurrent (2a \|\| 2b) | ~5-8s |
# MAGIC | **Routing model** | Claude Sonnet (3-4s) | Fine-tuned classifier or Haiku (<1s) | ~2-3s |
# MAGIC | **Pre-computed views** | LLM generates SQL at query time | Materialised Delta views, direct lookup | ~4-8s |
# MAGIC | **Response streaming** | Wait for full response | First tokens in <2s | Perceived: ~10s |
# MAGIC | **Query cache** | Every query hits LLM | Cache repeated patterns in Delta | 0s for cache hits |
# MAGIC | **Native SQL tools** | LLM generates SQL text | Agent Framework SQL tool binding | ~3-5s |
# MAGIC
# MAGIC ### Projected Production Latency
# MAGIC
# MAGIC | Query type | Current | Optimised | With streaming |
# MAGIC |-----------|---------|-----------|---------------|
# MAGIC | Simple text (KA only) | 8-14s | 4-6s | First token: <2s |
# MAGIC | Simple data (SQL only) | 10-18s | 2-4s | First token: <2s |
# MAGIC | Complex (both tools) | 18-35s | 5-8s | First token: <2s |
# MAGIC | Cached query | 10-18s | <1s | Instant |

# COMMAND ----------

# MAGIC %md
# MAGIC ### Key Production Optimisations in Detail
# MAGIC
# MAGIC **1. Pre-computed graph views** — The biggest win. Instead of the agent generating SQL at query time:
# MAGIC ```sql
# MAGIC -- Materialise a policy summary view (refreshed on pipeline run)
# MAGIC CREATE OR REPLACE TABLE insurance_poc.policy_summary AS
# MAGIC SELECT
# MAGIC   p.node_id,
# MAGIC   p.properties:policy_number AS policy_number,
# MAGIC   p.properties:class_of_business AS class_of_business,
# MAGIC   b.properties:name AS broker_name,
# MAGIC   i.properties:name AS insured_name,
# MAGIC   collect_list(DISTINCT l.properties:amount) AS limits,
# MAGIC   collect_list(DISTINCT ex.properties:title) AS exclusions
# MAGIC FROM graph_nodes p
# MAGIC LEFT JOIN graph_edges eb ON p.node_id = eb.source_id AND eb.relationship_type = 'PLACED_BY'
# MAGIC LEFT JOIN graph_nodes b ON eb.target_id = b.node_id
# MAGIC -- ... more joins
# MAGIC WHERE p.label = 'Policy'
# MAGIC GROUP BY p.node_id, ...
# MAGIC ```
# MAGIC The agent then queries a flat table — no joins, no JSON extraction, sub-second.
# MAGIC
# MAGIC **2. Genie / AI-BI for structured data** — Instead of a custom SQL agent, use Databricks Genie rooms backed by the materialised views. Genie handles NL-to-SQL natively with optimised query planning and caching.
# MAGIC
# MAGIC **3. Streaming responses** — The model serving endpoint supports SSE streaming. Users see the first tokens within 1-2s even if the full answer takes 8s. This is the single biggest improvement to perceived latency.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Comparison — Delta Tables vs Graph Database
# MAGIC
# MAGIC ### The question: "Should we use Neo4j / Neptune / TigerGraph instead?"
# MAGIC
# MAGIC | Dimension | Delta Tables (this demo) | Dedicated Graph DB (e.g. Neo4j) |
# MAGIC |-----------|------------------------|-------------------------------|
# MAGIC | **Simple traversal** (1-2 hops) | ~0.5-2s via SQL joins | ~5-50ms via Cypher |
# MAGIC | **Deep traversal** (5+ hops) | Degrades with recursive CTEs | Constant — native graph engine |
# MAGIC | **Aggregations** (sum limits, count policies) | Native SQL — fast | Requires full scan or plugin |
# MAGIC | **Full-text search** | Needs separate index or KA | Needs separate index (Lucene) |
# MAGIC | **Vector search** | Knowledge Assistants / VS indexes | Requires external system |
# MAGIC | **Schema evolution** | ALTER TABLE, instant | Schema migration scripts |
# MAGIC | **ACID transactions** | Native (Delta) | Varies by product |
# MAGIC | **Time travel / audit** | Native (Delta versions) | Manual snapshotting |
# MAGIC | **Row/column security** | Unity Catalog native | Application-level |
# MAGIC | **Lineage & governance** | Unity Catalog native | External tooling |
# MAGIC | **Operational cost** | Zero — part of Databricks | Separate cluster + licensing |
# MAGIC | **Query language** | SQL (universal) | Cypher/Gremlin (specialised) |
# MAGIC | **Scales to billions of edges** | Yes (distributed Delta) | Yes (clustered graph DB) |

# COMMAND ----------

# MAGIC %md
# MAGIC ### When a graph database wins
# MAGIC
# MAGIC A dedicated graph database outperforms Delta tables when:
# MAGIC
# MAGIC 1. **Deep multi-hop traversals are the primary query pattern** — e.g. "Find all reinsurance chains 6+ levels deep" or "What is the shortest path between Insurer A and Broker B through shared syndicates?" These recursive queries degrade exponentially in SQL but are constant-time in a graph engine.
# MAGIC
# MAGIC 2. **Real-time graph algorithms** — PageRank, community detection, shortest path, centrality. Graph databases have native implementations; SQL requires complex iterative CTEs.
# MAGIC
# MAGIC 3. **Massive scale with sparse connectivity** — When you have billions of nodes with an average of 2-3 edges each, graph databases' adjacency list storage is more efficient than join-based traversal.
# MAGIC
# MAGIC ### When Delta tables win
# MAGIC
# MAGIC Delta tables are the better choice when:
# MAGIC
# MAGIC 1. **Most queries are 1-2 hop traversals** — "Which broker placed this policy?" is a single JOIN. This is the majority of insurance use cases.
# MAGIC
# MAGIC 2. **Aggregations and analytics matter** — "Total limits by class of business" or "Average premium by broker" are SQL bread-and-butter. Graph databases struggle here.
# MAGIC
# MAGIC 3. **Governance is non-negotiable** — In a regulated Lloyd's syndicate, Unity Catalog's lineage, access control, audit logging, and model governance are table stakes. No graph database offers this natively.
# MAGIC
# MAGIC 4. **The graph is one layer of a larger platform** — The same data feeds dashboards, ML models, regulatory reports, and AI agents. Delta tables are the natural foundation.
# MAGIC
# MAGIC 5. **Operational simplicity** — No additional infrastructure to manage, monitor, back up, or secure.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Latency Comparison — Same Queries
# MAGIC
# MAGIC Let's compare the actual query patterns from our insurance demo:

# COMMAND ----------

# MAGIC %md
# MAGIC #### Query 1: "Which broker placed policy MRC-2025-LL-003?" (1-hop traversal)

# COMMAND ----------

t1, r1 = timed_sql("""
SELECT p.properties:policy_number AS policy, b.properties:name AS broker
FROM lr_serverless_aws_us_catalog.insurance_poc.graph_edges e
JOIN lr_serverless_aws_us_catalog.insurance_poc.graph_nodes p ON e.source_id = p.node_id
JOIN lr_serverless_aws_us_catalog.insurance_poc.graph_nodes b ON e.target_id = b.node_id
WHERE e.relationship_type = 'PLACED_BY' AND p.properties:policy_number = 'MRC-2025-LL-003'
""")
print(f"Delta SQL:  {t1:.2f}s — Result: {r1}")
print(f"Neo4j est:  ~0.01s  — MATCH (p:Policy)-[:PLACED_BY]->(b:Broker) WHERE p.policy_number = 'MRC-2025-LL-003' RETURN b.name")
print(f"\nVerdict: Graph DB is ~{t1/0.01:.0f}x faster for single-hop, but both are sub-second and acceptable for interactive use.")

# COMMAND ----------

# MAGIC %md
# MAGIC #### Query 2: "Total limits across all cyber policies" (1-hop + aggregation)

# COMMAND ----------

t2, r2 = timed_sql("""
SELECT
  p.properties:policy_number AS policy,
  l.properties:amount AS amount,
  l.properties:currency AS currency,
  l.properties:type AS limit_type
FROM lr_serverless_aws_us_catalog.insurance_poc.graph_edges e
JOIN lr_serverless_aws_us_catalog.insurance_poc.graph_nodes p ON e.source_id = p.node_id
JOIN lr_serverless_aws_us_catalog.insurance_poc.graph_nodes l ON e.target_id = l.node_id
WHERE e.relationship_type = 'HAS_LIMIT' AND p.properties:class_of_business LIKE '%Cyber%'
""")
print(f"Delta SQL:  {t2:.2f}s — {len(r2)} rows")
for row in r2:
    print(f"  {row}")
print(f"Neo4j est:  ~0.05s  — MATCH (p:Policy)-[:HAS_LIMIT]->(l:Limit) WHERE p.class_of_business CONTAINS 'Cyber' RETURN sum(toInteger(l.amount))")
print(f"\nVerdict: Delta is strong here — native aggregation. Neo4j needs post-processing for sums.")

# COMMAND ----------

# MAGIC %md
# MAGIC #### Query 3: "Find all syndicates that share policies with Aon" (2-hop traversal)

# COMMAND ----------

t3, r3 = timed_sql("""
SELECT DISTINCT ins.properties:name AS syndicate
FROM lr_serverless_aws_us_catalog.insurance_poc.graph_edges eb
JOIN lr_serverless_aws_us_catalog.insurance_poc.graph_nodes b ON eb.target_id = b.node_id
JOIN lr_serverless_aws_us_catalog.insurance_poc.graph_edges ei ON eb.source_id = ei.source_id
JOIN lr_serverless_aws_us_catalog.insurance_poc.graph_nodes ins ON ei.target_id = ins.node_id
WHERE eb.relationship_type = 'PLACED_BY'
  AND b.properties:name LIKE '%Aon%'
  AND ei.relationship_type = 'UNDERWRITTEN_BY'
""")
print(f"Delta SQL:  {t3:.2f}s — {len(r3)} syndicates")
for row in r3:
    print(f"  {row[0]}")
print(f"Neo4j est:  ~0.02s  — MATCH (b:Broker)<-[:PLACED_BY]-(p:Policy)-[:UNDERWRITTEN_BY]->(i:Insurer) WHERE b.name CONTAINS 'Aon' RETURN DISTINCT i.name")
print(f"\nVerdict: 2-hop is where graph DBs shine in readability. Delta handles it fine at this scale but Cypher is more intuitive.")

# COMMAND ----------

# MAGIC %md
# MAGIC #### Query 4 (hypothetical): "Find all reinsurance chains 5 levels deep"

# COMMAND ----------

# MAGIC %md
# MAGIC ```
# MAGIC -- Delta SQL: Recursive CTE (complex, slow at depth)
# MAGIC WITH RECURSIVE chain AS (
# MAGIC   SELECT source_id, target_id, 1 AS depth
# MAGIC   FROM graph_edges WHERE relationship_type = 'REINSURED_BY'
# MAGIC   UNION ALL
# MAGIC   SELECT c.source_id, e.target_id, c.depth + 1
# MAGIC   FROM chain c JOIN graph_edges e ON c.target_id = e.source_id
# MAGIC   WHERE e.relationship_type = 'REINSURED_BY' AND c.depth < 5
# MAGIC )
# MAGIC SELECT * FROM chain WHERE depth = 5
# MAGIC
# MAGIC -- Neo4j Cypher: Native and fast
# MAGIC MATCH path = (p:Policy)-[:REINSURED_BY*5]->(r:Reinsurer)
# MAGIC RETURN path
# MAGIC
# MAGIC -- Estimated latency:
# MAGIC --   Delta: 2-10s depending on data volume (recursive CTEs are expensive)
# MAGIC --   Neo4j: ~0.05s (native graph traversal, constant time per hop)
# MAGIC ```
# MAGIC
# MAGIC **This is where a graph database genuinely wins.** Deep recursive traversals are its core strength. If your primary use case is reinsurance chain analysis, retrocession mapping, or network-of-networks queries, a graph DB is justified.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Recommendation for Production
# MAGIC
# MAGIC | Use case | Recommendation | Why |
# MAGIC |----------|---------------|-----|
# MAGIC | **Policy Q&A assistant** (this demo) | Delta tables + Databricks agents | 1-2 hop queries, governance matters, aggregations common |
# MAGIC | **Reinsurance chain analysis** | Graph DB (Neo4j/Neptune) + Delta lakehouse | Deep traversals are the primary pattern |
# MAGIC | **Fraud network detection** | Graph DB for detection, Delta for reporting | Community detection algorithms need native graph |
# MAGIC | **Regulatory reporting** | Delta tables only | SQL aggregations, audit trail, time travel |
# MAGIC | **Real-time exposure monitoring** | Materialised Delta views + streaming | Sub-second updates, SQL analytics |
# MAGIC
# MAGIC ### Hybrid Architecture (Best of Both)
# MAGIC
# MAGIC For a Lloyd's syndicate that needs both:
# MAGIC
# MAGIC ```
# MAGIC ┌─────────────────────────────────────────────────────────────┐
# MAGIC │                    DATABRICKS LAKEHOUSE                     │
# MAGIC │                                                             │
# MAGIC │  Raw PDFs → ai_parse → ai_query → Delta graph tables       │
# MAGIC │                                     │                       │
# MAGIC │                          ┌──────────┴──────────┐            │
# MAGIC │                          ▼                     ▼            │
# MAGIC │                    Delta Tables          Graph DB sync      │
# MAGIC │                   (governance,           (Neo4j/Neptune)    │
# MAGIC │                    reporting,                  │            │
# MAGIC │                    aggregations)               │            │
# MAGIC │                          │              Deep traversals     │
# MAGIC │                          │              Network algorithms  │
# MAGIC │                          │                     │            │
# MAGIC │                          └──────────┬──────────┘            │
# MAGIC │                                     ▼                       │
# MAGIC │                              Supervisor Agent               │
# MAGIC │                         (routes to right backend)           │
# MAGIC │                                                             │
# MAGIC │  Governance: Unity Catalog (single source of truth)         │
# MAGIC │  The graph DB is a read replica — Delta is authoritative    │
# MAGIC └─────────────────────────────────────────────────────────────┘
# MAGIC ```
# MAGIC
# MAGIC **Key principle**: Delta is the authoritative store (governance, lineage, audit). The graph DB is a **read-optimised projection** synced from Delta, used only for queries that genuinely need deep traversal. The supervisor agent routes to whichever backend is best for the query.
# MAGIC
# MAGIC This gives you:
# MAGIC - Sub-second deep traversals (graph DB)
# MAGIC - Native governance and audit (Unity Catalog)
# MAGIC - SQL analytics and aggregations (Delta)
# MAGIC - Single source of truth (no data inconsistency)
