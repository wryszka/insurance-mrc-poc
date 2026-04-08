# Databricks notebook source
# MAGIC %md
# MAGIC # Security, Audit & Governance — Regulated Lloyd's Syndicate
# MAGIC
# MAGIC **Demo scenario**: A Lloyd's syndicate operates under PRA/FCA regulation. Every AI system must demonstrate:
# MAGIC - **Auditability**: Who queried what, when, and what was returned
# MAGIC - **Data lineage**: Where did each answer come from
# MAGIC - **Access control**: Who can see which policies
# MAGIC - **Model governance**: Which models are in production, who approved them
# MAGIC - **Inference logging**: Full request/response capture for compliance
# MAGIC
# MAGIC This notebook shows how Databricks provides all of this **out of the box**.
# MAGIC
# MAGIC > **Disclaimer**: This is not a Databricks product. Data is synthetic. Provided as-is for demonstration purposes.
# MAGIC
# MAGIC ---

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Inference Logging — Every Question and Answer Recorded
# MAGIC
# MAGIC The AI Gateway automatically logs every request and response to an **inference table**. This is tamper-proof and queryable.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Inference table: every request/response to the supervisor agent
# MAGIC SELECT
# MAGIC   request_time,
# MAGIC   status_code,
# MAGIC   execution_duration_ms,
# MAGIC   LEFT(request, 200) AS request_preview,
# MAGIC   LEFT(response, 200) AS response_preview
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_poc.insurance_supervisor_agent_payload
# MAGIC ORDER BY request_time DESC
# MAGIC LIMIT 10

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Request volume and latency over time
# MAGIC SELECT
# MAGIC   date_trunc('hour', request_time) AS hour,
# MAGIC   COUNT(*) AS request_count,
# MAGIC   AVG(execution_duration_ms) AS avg_latency_ms,
# MAGIC   MAX(execution_duration_ms) AS max_latency_ms
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_poc.insurance_supervisor_agent_payload
# MAGIC GROUP BY 1
# MAGIC ORDER BY 1 DESC

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Endpoint Usage & Token Tracking
# MAGIC
# MAGIC Every serving endpoint call is tracked in the system tables — including token counts for cost monitoring.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Endpoint usage: requests, tokens, and status codes
# MAGIC SELECT
# MAGIC   e.endpoint_name,
# MAGIC   date_trunc('hour', u.request_time) AS hour,
# MAGIC   COUNT(*) AS requests,
# MAGIC   SUM(u.input_token_count) AS total_input_tokens,
# MAGIC   SUM(u.output_token_count) AS total_output_tokens,
# MAGIC   SUM(u.input_token_count + u.output_token_count) AS total_tokens
# MAGIC FROM system.serving.endpoint_usage u
# MAGIC JOIN system.serving.served_entities e ON u.served_entity_id = e.served_entity_id
# MAGIC WHERE e.endpoint_name LIKE '%insurance%' OR e.endpoint_name LIKE '%ka-04bfe%'
# MAGIC GROUP BY 1, 2
# MAGIC ORDER BY 2 DESC

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Served entities: what models are deployed and who created them
# MAGIC SELECT
# MAGIC   endpoint_name,
# MAGIC   served_entity_name,
# MAGIC   entity_name,
# MAGIC   entity_version,
# MAGIC   created_by,
# MAGIC   change_time
# MAGIC FROM system.serving.served_entities
# MAGIC WHERE endpoint_name LIKE '%insurance%' OR endpoint_name LIKE '%ka-04bfe%'
# MAGIC ORDER BY change_time DESC

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Unity Catalog Lineage — Data Provenance
# MAGIC
# MAGIC Unity Catalog automatically tracks **lineage** — which tables were read/written, by which jobs, using which code.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Table lineage: what wrote to the graph tables and when?
# MAGIC SELECT
# MAGIC   entity_type,
# MAGIC   source_table_full_name,
# MAGIC   target_table_full_name,
# MAGIC   created_by,
# MAGIC   event_time
# MAGIC FROM system.access.table_lineage
# MAGIC WHERE target_table_full_name LIKE '%insurance_poc.graph%'
# MAGIC ORDER BY event_time DESC
# MAGIC LIMIT 20

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Column-level lineage
# MAGIC SELECT
# MAGIC   source_table_full_name,
# MAGIC   source_column_name,
# MAGIC   target_table_full_name,
# MAGIC   target_column_name,
# MAGIC   event_time
# MAGIC FROM system.access.column_lineage
# MAGIC WHERE target_table_full_name LIKE '%insurance_poc.graph%'
# MAGIC ORDER BY event_time DESC
# MAGIC LIMIT 20

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Audit Logs — Who Did What
# MAGIC
# MAGIC The system audit log captures every action: schema creation, table access, endpoint queries, file uploads.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Audit trail: all actions on insurance_poc schema objects
# MAGIC SELECT
# MAGIC   event_time,
# MAGIC   user_identity.email AS user_email,
# MAGIC   service_name,
# MAGIC   action_name,
# MAGIC   request_params['full_name_arg'] AS resource,
# MAGIC   response.status_code AS status
# MAGIC FROM system.access.audit
# MAGIC WHERE request_params['full_name_arg'] LIKE '%insurance_poc%'
# MAGIC   AND event_date > current_date() - INTERVAL 7 DAYS
# MAGIC ORDER BY event_time DESC
# MAGIC LIMIT 30

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Who accessed the serving endpoints?
# MAGIC SELECT
# MAGIC   event_time,
# MAGIC   user_identity.email AS user_email,
# MAGIC   action_name,
# MAGIC   request_params['name'] AS endpoint_name,
# MAGIC   source_ip_address
# MAGIC FROM system.access.audit
# MAGIC WHERE service_name = 'serving'
# MAGIC   AND event_date > current_date() - INTERVAL 7 DAYS
# MAGIC ORDER BY event_time DESC
# MAGIC LIMIT 20

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Access Control — Row-Level and Column-Level Security
# MAGIC
# MAGIC Unity Catalog provides fine-grained access control. In a Lloyd's syndicate:
# MAGIC - **Underwriters** can see all policies they manage
# MAGIC - **Brokers** can only see policies they placed
# MAGIC - **Claims team** can see limits and deductibles but not premium amounts
# MAGIC - **Compliance** can see everything including audit logs

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Current grants on the insurance_poc schema
# MAGIC SHOW GRANTS ON SCHEMA lr_serverless_aws_us_catalog.insurance_poc

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Current grants on graph tables
# MAGIC SHOW GRANTS ON TABLE lr_serverless_aws_us_catalog.insurance_poc.graph_nodes

# COMMAND ----------

# MAGIC %md
# MAGIC ### Example: Row-Level Security for Broker Access
# MAGIC
# MAGIC A broker should only see policies they placed. We can enforce this with a **row filter**.
# MAGIC
# MAGIC ```sql
# MAGIC -- Example row filter function (not applied in this demo)
# MAGIC CREATE FUNCTION insurance_poc.broker_row_filter(source_id STRING, relationship_type STRING)
# MAGIC RETURNS BOOLEAN
# MAGIC RETURN
# MAGIC   CASE
# MAGIC     WHEN is_member('brokers_group') THEN
# MAGIC       EXISTS (
# MAGIC         SELECT 1 FROM insurance_poc.graph_edges e
# MAGIC         JOIN insurance_poc.graph_nodes b ON e.target_id = b.node_id
# MAGIC         WHERE e.relationship_type = 'PLACED_BY'
# MAGIC           AND e.source_id = source_id
# MAGIC           AND b.properties:name = current_user()
# MAGIC       )
# MAGIC     ELSE TRUE
# MAGIC   END;
# MAGIC
# MAGIC ALTER TABLE insurance_poc.graph_edges SET ROW FILTER broker_row_filter ON (source_id, relationship_type);
# MAGIC ```
# MAGIC
# MAGIC ### Example: Column Masking for Sensitive Fields
# MAGIC
# MAGIC Premium amounts may be confidential. We can mask them for certain roles.
# MAGIC
# MAGIC ```sql
# MAGIC CREATE FUNCTION insurance_poc.mask_premium(properties STRING)
# MAGIC RETURNS STRING
# MAGIC RETURN
# MAGIC   CASE
# MAGIC     WHEN is_member('premium_viewers') THEN properties
# MAGIC     ELSE regexp_replace(properties, '"amount":\s*"[^"]*"', '"amount": "REDACTED"')
# MAGIC   END;
# MAGIC
# MAGIC ALTER TABLE insurance_poc.graph_nodes ALTER COLUMN properties SET MASK mask_premium;
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Model Governance — Unity Catalog Model Registry
# MAGIC
# MAGIC All models are registered in Unity Catalog with full version history and lineage.
# MAGIC
# MAGIC The supervisor agent model is registered at: `<your_catalog>.insurance_poc.insurance_supervisor_agent`
# MAGIC
# MAGIC To inspect versions, approvals, and lineage:
# MAGIC 1. Navigate to **Catalog** in the sidebar
# MAGIC 2. Browse to your catalog → `insurance_poc` → **Models** → `insurance_supervisor_agent`
# MAGIC 3. Each version shows: creation time, source run, serving endpoints, and lineage graph
# MAGIC
# MAGIC In production, you would use **model aliases** (e.g. `@champion`, `@challenger`) and **approval workflows** to control which version serves traffic.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Data Quality — Graph Integrity Checks
# MAGIC
# MAGIC For a regulated environment, we need to verify the knowledge graph is consistent and complete.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Integrity checks
# MAGIC SELECT 'Orphaned source' AS issue, COUNT(*) AS count
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_poc.graph_edges e
# MAGIC LEFT JOIN lr_serverless_aws_us_catalog.insurance_poc.graph_nodes n ON e.source_id = n.node_id
# MAGIC WHERE n.node_id IS NULL
# MAGIC UNION ALL
# MAGIC SELECT 'Orphaned target', COUNT(*)
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_poc.graph_edges e
# MAGIC LEFT JOIN lr_serverless_aws_us_catalog.insurance_poc.graph_nodes n ON e.target_id = n.node_id
# MAGIC WHERE n.node_id IS NULL
# MAGIC UNION ALL
# MAGIC SELECT 'Policy without broker', COUNT(*)
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_poc.graph_nodes p
# MAGIC LEFT JOIN lr_serverless_aws_us_catalog.insurance_poc.graph_edges e
# MAGIC   ON p.node_id = e.source_id AND e.relationship_type = 'PLACED_BY'
# MAGIC WHERE p.label = 'Policy' AND e.source_id IS NULL
# MAGIC UNION ALL
# MAGIC SELECT 'Policy without limits', COUNT(*)
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_poc.graph_nodes p
# MAGIC LEFT JOIN lr_serverless_aws_us_catalog.insurance_poc.graph_edges e
# MAGIC   ON p.node_id = e.source_id AND e.relationship_type = 'HAS_LIMIT'
# MAGIC WHERE p.label = 'Policy' AND e.source_id IS NULL

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Delta table history — full change log
# MAGIC DESCRIBE HISTORY lr_serverless_aws_us_catalog.insurance_poc.graph_nodes

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Time travel: see the graph at any point in time
# MAGIC SELECT COUNT(*) AS nodes_at_version_0
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_poc.graph_nodes VERSION AS OF 0

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Daily Pipeline Health Summary

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   current_date() AS report_date,
# MAGIC   (SELECT COUNT(*) FROM lr_serverless_aws_us_catalog.insurance_poc.graph_nodes) AS total_nodes,
# MAGIC   (SELECT COUNT(*) FROM lr_serverless_aws_us_catalog.insurance_poc.graph_edges) AS total_edges,
# MAGIC   (SELECT COUNT(DISTINCT label) FROM lr_serverless_aws_us_catalog.insurance_poc.graph_nodes) AS entity_types,
# MAGIC   (SELECT COUNT(DISTINCT relationship_type) FROM lr_serverless_aws_us_catalog.insurance_poc.graph_edges) AS relationship_types,
# MAGIC   (SELECT COUNT(*) FROM lr_serverless_aws_us_catalog.insurance_poc.graph_nodes WHERE label = 'Policy') AS policies_indexed

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary — Governance Controls
# MAGIC
# MAGIC | Requirement | Databricks Feature | Status |
# MAGIC |------------|-------------------|--------|
# MAGIC | **Audit trail** | `system.access.audit` | Automatic, tamper-proof |
# MAGIC | **Inference logging** | AI Gateway inference table | Every request/response captured |
# MAGIC | **Token/cost tracking** | `system.serving.endpoint_usage` | Per-endpoint token counts |
# MAGIC | **Data lineage** | `system.access.table_lineage` + `column_lineage` | Automatic column-level tracking |
# MAGIC | **Access control** | UC grants + row/column security | Configurable per group |
# MAGIC | **Model governance** | UC Model Registry (via MLflow) | Version history, approval workflow |
# MAGIC | **Data versioning** | Delta Lake time travel | Full history, point-in-time queries |
# MAGIC | **Encryption** | Platform default | At-rest and in-transit |
# MAGIC | **Network isolation** | Private Link / VPC | Configurable per workspace |
# MAGIC
# MAGIC > **For a Lloyd's syndicate under PRA/FCA supervision**: every query, every extraction, every model change is logged, versioned, and auditable. The entire AI pipeline runs within a single governed platform — no data leaves Databricks.
