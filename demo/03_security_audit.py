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
# MAGIC ---

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Inference Logging — Every Question and Answer Recorded
# MAGIC
# MAGIC Databricks Model Serving automatically logs every request and response to a **system table**. This is non-optional, tamper-proof, and queryable.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- All inference requests to our supervisor agent (last 24 hours)
# MAGIC SELECT
# MAGIC   request_time,
# MAGIC   request_metadata.endpoint_name,
# MAGIC   request_metadata.model_name,
# MAGIC   request,
# MAGIC   response,
# MAGIC   execution_time_ms
# MAGIC FROM system.serving.served_model_requests
# MAGIC WHERE request_metadata.endpoint_name LIKE '%insurance%'
# MAGIC   AND request_time > current_timestamp() - INTERVAL 24 HOURS
# MAGIC ORDER BY request_time DESC
# MAGIC LIMIT 20

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Request volume by hour — detect unusual activity patterns
# MAGIC SELECT
# MAGIC   date_trunc('hour', request_time) AS hour,
# MAGIC   request_metadata.endpoint_name,
# MAGIC   COUNT(*) AS request_count,
# MAGIC   AVG(execution_time_ms) AS avg_latency_ms,
# MAGIC   MAX(execution_time_ms) AS max_latency_ms
# MAGIC FROM system.serving.served_model_requests
# MAGIC WHERE request_metadata.endpoint_name LIKE '%insurance%'
# MAGIC   AND request_time > current_timestamp() - INTERVAL 7 DAYS
# MAGIC GROUP BY 1, 2
# MAGIC ORDER BY 1 DESC

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. AI Gateway Guardrails — Token Usage and Cost Tracking
# MAGIC
# MAGIC Every `ai_query()` call is tracked through the AI Gateway. We can see exactly how many tokens the extraction pipeline consumed.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- AI Gateway usage: token consumption by model
# MAGIC SELECT
# MAGIC   date_trunc('day', usage_date) AS day,
# MAGIC   account_id,
# MAGIC   workspace_id,
# MAGIC   endpoint_name,
# MAGIC   SUM(input_token_count) AS total_input_tokens,
# MAGIC   SUM(output_token_count) AS total_output_tokens,
# MAGIC   SUM(input_token_count + output_token_count) AS total_tokens
# MAGIC FROM system.serving.served_model_token_usage
# MAGIC WHERE endpoint_name LIKE '%llama%' OR endpoint_name LIKE '%claude%'
# MAGIC   AND usage_date > current_date() - INTERVAL 7 DAYS
# MAGIC GROUP BY 1, 2, 3, 4
# MAGIC ORDER BY 1 DESC

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Unity Catalog Lineage — Data Provenance
# MAGIC
# MAGIC Unity Catalog automatically tracks **lineage** — which tables were read/written, by which jobs, using which code.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Table lineage: who wrote to the graph tables and when?
# MAGIC SELECT
# MAGIC   entity_type,
# MAGIC   entity_name,
# MAGIC   source_type,
# MAGIC   source_name,
# MAGIC   event_time,
# MAGIC   event_type
# MAGIC FROM system.access.table_lineage
# MAGIC WHERE target_table_full_name LIKE '%insurance_mrc_assistant.graph%'
# MAGIC   AND event_time > current_timestamp() - INTERVAL 7 DAYS
# MAGIC ORDER BY event_time DESC
# MAGIC LIMIT 20

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Audit Logs — Who Did What
# MAGIC
# MAGIC The system audit log captures every action: schema creation, table access, endpoint queries, file uploads.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Audit trail: all actions on insurance_mrc_assistant schema objects
# MAGIC SELECT
# MAGIC   event_time,
# MAGIC   user_identity.email AS user,
# MAGIC   action_name,
# MAGIC   request_params.full_name_arg AS resource,
# MAGIC   response.status_code
# MAGIC FROM system.access.audit
# MAGIC WHERE request_params.full_name_arg LIKE '%insurance_mrc_assistant%'
# MAGIC   AND event_time > current_timestamp() - INTERVAL 7 DAYS
# MAGIC ORDER BY event_time DESC
# MAGIC LIMIT 30

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Who accessed the serving endpoints?
# MAGIC SELECT
# MAGIC   event_time,
# MAGIC   user_identity.email AS user,
# MAGIC   action_name,
# MAGIC   request_params.name AS endpoint,
# MAGIC   source_ip_address
# MAGIC FROM system.access.audit
# MAGIC WHERE action_name LIKE '%servingEndpoints%'
# MAGIC   AND request_params.name LIKE '%insurance%'
# MAGIC   AND event_time > current_timestamp() - INTERVAL 7 DAYS
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
# MAGIC -- Current grants on the insurance_mrc_assistant schema
# MAGIC SHOW GRANTS ON SCHEMA lr_serverless_aws_us_catalog.insurance_mrc_assistant

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Current grants on graph tables
# MAGIC SHOW GRANTS ON TABLE lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes

# COMMAND ----------

# MAGIC %md
# MAGIC ### Example: Row-Level Security for Broker Access
# MAGIC
# MAGIC A broker should only see policies they placed. We can enforce this with a **row filter**.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Create a row filter function (example — not applied in demo)
# MAGIC -- In production, this restricts what each user can see
# MAGIC CREATE OR REPLACE FUNCTION lr_serverless_aws_us_catalog.insurance_mrc_assistant.broker_row_filter(source_id STRING, relationship_type STRING)
# MAGIC RETURNS BOOLEAN
# MAGIC RETURN
# MAGIC   CASE
# MAGIC     -- If the user is a broker, only show edges related to their policies
# MAGIC     WHEN is_member('brokers_group') THEN
# MAGIC       EXISTS (
# MAGIC         SELECT 1 FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_edges e
# MAGIC         JOIN lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes b ON e.target_id = b.node_id
# MAGIC         WHERE e.relationship_type = 'PLACED_BY'
# MAGIC           AND e.source_id = source_id
# MAGIC           AND b.properties:name = current_user()
# MAGIC       )
# MAGIC     ELSE TRUE  -- Non-brokers see everything
# MAGIC   END

# COMMAND ----------

# MAGIC %md
# MAGIC ### Example: Column Masking for Sensitive Fields
# MAGIC
# MAGIC Premium amounts may be confidential. We can mask them for certain roles.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Column mask function (example)
# MAGIC CREATE OR REPLACE FUNCTION lr_serverless_aws_us_catalog.insurance_mrc_assistant.mask_premium(properties STRING)
# MAGIC RETURNS STRING
# MAGIC RETURN
# MAGIC   CASE
# MAGIC     WHEN is_member('premium_viewers') THEN properties
# MAGIC     ELSE regexp_replace(properties, '"amount":\\s*"[^"]*"', '"amount": "REDACTED"')
# MAGIC   END

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Model Registry & Governance
# MAGIC
# MAGIC All models are registered in Unity Catalog with full version history, lineage, and approval workflows.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- All registered models in the insurance_mrc_assistant schema
# MAGIC SELECT
# MAGIC   catalog_name,
# MAGIC   schema_name,
# MAGIC   name AS model_name,
# MAGIC   comment
# MAGIC FROM system.information_schema.registered_models
# MAGIC WHERE schema_name = 'insurance_mrc_assistant'

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Model versions and their deployment status
# MAGIC SELECT
# MAGIC   model_name,
# MAGIC   version,
# MAGIC   source,
# MAGIC   status,
# MAGIC   created_at,
# MAGIC   last_updated_at
# MAGIC FROM system.information_schema.model_versions
# MAGIC WHERE schema_name = 'insurance_mrc_assistant'
# MAGIC ORDER BY model_name, version DESC

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Data Quality — Graph Integrity Checks
# MAGIC
# MAGIC For a regulated environment, we need to verify the knowledge graph is consistent and complete.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Integrity check: orphaned edges (edges pointing to non-existent nodes)
# MAGIC SELECT 'Orphaned source' AS issue, COUNT(*) AS count
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_edges e
# MAGIC LEFT JOIN lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes n ON e.source_id = n.node_id
# MAGIC WHERE n.node_id IS NULL
# MAGIC UNION ALL
# MAGIC SELECT 'Orphaned target', COUNT(*)
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_edges e
# MAGIC LEFT JOIN lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes n ON e.target_id = n.node_id
# MAGIC WHERE n.node_id IS NULL
# MAGIC UNION ALL
# MAGIC -- Policies without a broker (every policy must be placed)
# MAGIC SELECT 'Policy without broker', COUNT(*)
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes p
# MAGIC LEFT JOIN lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_edges e
# MAGIC   ON p.node_id = e.source_id AND e.relationship_type = 'PLACED_BY'
# MAGIC WHERE p.label = 'Policy' AND e.source_id IS NULL
# MAGIC UNION ALL
# MAGIC -- Policies without any limits
# MAGIC SELECT 'Policy without limits', COUNT(*)
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes p
# MAGIC LEFT JOIN lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_edges e
# MAGIC   ON p.node_id = e.source_id AND e.relationship_type = 'HAS_LIMIT'
# MAGIC WHERE p.label = 'Policy' AND e.source_id IS NULL

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Delta table history — full change log, who wrote what when
# MAGIC DESCRIBE HISTORY lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Time travel: see the graph at any point in time
# MAGIC SELECT COUNT(*) AS nodes_at_version_0
# MAGIC FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes VERSION AS OF 0

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Monitoring Dashboard Queries
# MAGIC
# MAGIC These queries can be pinned to an AI/BI Dashboard for ongoing monitoring.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Daily pipeline health summary
# MAGIC SELECT
# MAGIC   current_date() AS report_date,
# MAGIC   (SELECT COUNT(*) FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes) AS total_nodes,
# MAGIC   (SELECT COUNT(*) FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_edges) AS total_edges,
# MAGIC   (SELECT COUNT(DISTINCT label) FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes) AS entity_types,
# MAGIC   (SELECT COUNT(DISTINCT relationship_type) FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_edges) AS relationship_types,
# MAGIC   (SELECT COUNT(*) FROM lr_serverless_aws_us_catalog.insurance_mrc_assistant.graph_nodes WHERE label = 'Policy') AS policies_indexed

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary — Governance Controls
# MAGIC
# MAGIC | Requirement | Databricks Feature | Status |
# MAGIC |------------|-------------------|--------|
# MAGIC | **Audit trail** | System tables (`system.access.audit`) | Automatic, tamper-proof |
# MAGIC | **Inference logging** | Served model requests table | Every request/response captured |
# MAGIC | **Data lineage** | Unity Catalog lineage | Automatic column-level tracking |
# MAGIC | **Access control** | UC grants + row/column security | Configurable per group |
# MAGIC | **Model governance** | UC Model Registry | Version history, approval workflow |
# MAGIC | **Data versioning** | Delta Lake time travel | Full history, point-in-time queries |
# MAGIC | **Token/cost tracking** | AI Gateway usage tables | Per-model, per-endpoint |
# MAGIC | **Data quality** | SQL assertions on graph | Automated integrity checks |
# MAGIC | **Encryption** | Platform default | At-rest and in-transit |
# MAGIC | **Network isolation** | Private Link / VPC | Configurable per workspace |
# MAGIC
# MAGIC > **For a Lloyd's syndicate under PRA/FCA supervision**: every query, every extraction, every model change is logged, versioned, and auditable. The entire AI pipeline runs within a single governed platform — no data leaves Databricks.
