# Databricks notebook source
# MAGIC %md
# MAGIC # Insurance MRC POC - Agent Deployment
# MAGIC Registers and deploys the SQL Sub-Agent and Multi-Agent Supervisor.

# COMMAND ----------

# MAGIC %pip install databricks-agents mlflow databricks-sdk
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import json
import time
import mlflow
import databricks.agents as agents
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState
from mlflow.models.signature import ModelSignature
from mlflow.types.schema import ColSpec, Schema

mlflow.set_registry_uri("databricks-uc")

# Define model signature for chat-style agents
_input_schema = Schema([ColSpec("string", "messages")])
_output_schema = Schema([ColSpec("string", "content")])
AGENT_SIGNATURE = ModelSignature(inputs=_input_schema, outputs=_output_schema)

CATALOG = "lr_serverless_aws_us_catalog"
SCHEMA = "insurance_poc"
FULL_SCHEMA = CATALOG + "." + SCHEMA
LLM_ENDPOINT = "databricks-claude-sonnet-4-6"
KA_ENDPOINT = "ka-04bfe483-endpoint"

w = WorkspaceClient()
print("Connected as:", w.current_user.me().user_name)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Helper: SQL Execution Mixin

# COMMAND ----------

def get_warehouse_id():
    warehouses = list(w.warehouses.list())
    for wh in warehouses:
        if wh.enable_serverless_compute:
            return wh.id
    return warehouses[0].id if warehouses else None

def execute_sql(warehouse_id, sql):
    resp = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id, statement=sql.strip(), wait_timeout="50s"
    )
    while resp.status and resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(1)
        resp = w.statement_execution.get_statement(resp.statement_id)
    if resp.status and resp.status.state == StatementState.FAILED:
        raise RuntimeError("SQL failed: " + str(resp.status.error.message))
    return resp.result.data_array if resp.result and resp.result.data_array else []

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Register SQL Sub-Agent

# COMMAND ----------

SQL_AGENT_PROMPT = (
    "You are a graph data extractor. Write and execute SQL queries on the nodes "
    "and edges tables to find entity relationships and limits. Return facts.\n\n"
    "Available tables:\n"
    "- " + FULL_SCHEMA + ".graph_nodes (node_id STRING, label STRING, properties STRING)\n"
    "  Labels: Policy, Insured, Broker, Insurer, Limit, Deductible, Clause, Exclusion, Premium\n\n"
    "- " + FULL_SCHEMA + ".graph_edges (source_id STRING, target_id STRING, relationship_type STRING)\n"
    "  Relationships: PLACED_BY, ISSUED_TO, UNDERWRITTEN_BY, HAS_LIMIT, HAS_DEDUCTIBLE, CONTAINS_CLAUSE, EXCLUDES, HAS_PREMIUM\n\n"
    "The 'properties' column is a JSON string. Use the : operator to extract fields.\n"
    "Always return factual answers based on query results. Include the SQL you executed."
)

class SQLSubAgent(mlflow.pyfunc.PythonModel):

    def load_context(self, context):
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.sql import StatementState as SS
        self._w = WorkspaceClient()
        self._ss = SS
        whs = list(self._w.warehouses.list())
        self._wh_id = next((x.id for x in whs if x.enable_serverless_compute), whs[0].id if whs else None)

    def predict(self, context, model_input, params=None):
        import pandas as pd
        query = self._get_query(model_input)
        prompt = SQL_AGENT_PROMPT + "\n\nUser question: " + query + "\n\nGenerate SQL only, no explanation."
        sql_text = self._llm(prompt)
        sql_clean = self._strip_md(sql_text)
        try:
            rows = self._sql(sql_clean)
            if not rows:
                return {"content": "No results.\nSQL: " + sql_clean}
            display = "\n".join(str(r) for r in rows[:25])
            return {"content": "Results (" + str(len(rows)) + " rows):\n" + display + "\n\nSQL: " + sql_clean}
        except Exception as e:
            return {"content": "SQL error: " + str(e) + "\nSQL: " + sql_clean}

    def _get_query(self, mi):
        import pandas as pd
        if isinstance(mi, pd.DataFrame):
            recs = mi.to_dict(orient="records")
            if recs and "messages" in recs[0]:
                return recs[0]["messages"][-1].get("content", "")
            return str(mi.iloc[0, 0])
        if isinstance(mi, dict):
            msgs = mi.get("messages", [])
            return msgs[-1].get("content", "") if msgs else str(mi)
        return str(mi)

    def _llm(self, prompt):
        esc = prompt.replace("'", "''")
        r = self._sql("SELECT ai_query('" + LLM_ENDPOINT + "', '" + esc + "') AS r")
        return r[0][0] if r else ""

    def _strip_md(self, s):
        s = s.strip()
        if s.startswith("```"):
            lines = s.split("\n")
            return "\n".join(l for l in lines if not l.strip().startswith("```"))
        return s

    def _sql(self, q):
        import time as t
        r = self._w.statement_execution.execute_statement(
            warehouse_id=self._wh_id, statement=q.strip(), wait_timeout="50s")
        while r.status and r.status.state in (self._ss.PENDING, self._ss.RUNNING):
            t.sleep(1)
            r = self._w.statement_execution.get_statement(r.statement_id)
        if r.status and r.status.state == self._ss.FAILED:
            raise RuntimeError("SQL failed: " + str(r.status.error.message))
        return r.result.data_array if r.result and r.result.data_array else []


sql_model_name = FULL_SCHEMA + ".sql_sub_agent"

with mlflow.start_run(run_name="sql_sub_agent"):
    info = mlflow.pyfunc.log_model(
        artifact_path="sql_sub_agent",
        python_model=SQLSubAgent(),
        registered_model_name=sql_model_name,
        signature=AGENT_SIGNATURE,
        pip_requirements=["databricks-sdk", "mlflow"],
    )
    print("SQL Agent logged:", info.model_uri)

print("SQL Agent registered:", sql_model_name)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Register Multi-Agent Supervisor

# COMMAND ----------

SUPERVISOR_PROMPT = (
    "You are an insurance policy supervisor agent for Lloyd's Market Reform Contracts.\n\n"
    "You have two tools:\n"
    "TOOL A - Knowledge Assistant: clause text, policy wording, semantic search, unstructured content\n"
    "TOOL B - SQL Agent: limits, relationships, aggregations, structured graph data\n\n"
    "Routing rules:\n"
    "1. Text/semantics -> Tool A\n"
    "2. Relationships/math/structured -> Tool B\n"
    "3. Complex -> BOTH, synthesize answer\n\n"
    'Return routing JSON: {"tools": ["A"|"B"|both], "query": "...", "query_a": "...", "query_b": "..."}'
)


class SupervisorAgent(mlflow.pyfunc.PythonModel):

    def load_context(self, context):
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.sql import StatementState as SS
        self._w = WorkspaceClient()
        self._ss = SS
        whs = list(self._w.warehouses.list())
        self._wh_id = next((x.id for x in whs if x.enable_serverless_compute), whs[0].id if whs else None)

    def predict(self, context, model_input, params=None):
        query = self._get_query(model_input)

        # Route
        route_text = self._llm(SUPERVISOR_PROMPT + "\n\nUser: " + query + "\n\nReturn ONLY routing JSON.")
        routing = self._parse_route(route_text, query)

        results = {}
        if "A" in routing["tools"]:
            qa = routing.get("query_a", routing.get("query", query))
            results["Knowledge Assistant"] = self._call_ka(qa)
        if "B" in routing["tools"]:
            qb = routing.get("query_b", routing.get("query", query))
            results["SQL Agent"] = self._call_sql_agent(qb)

        # Synthesize
        parts = ["Original question: " + query + "\n"]
        for source, data in results.items():
            parts.append(source + ":\n" + data + "\n")
        parts.append("Provide a clear answer citing sources.")
        final = self._llm("\n".join(parts))
        return {"content": final}

    def _get_query(self, mi):
        import pandas as pd
        if isinstance(mi, pd.DataFrame):
            recs = mi.to_dict(orient="records")
            if recs and "messages" in recs[0]:
                return recs[0]["messages"][-1].get("content", "")
            return str(mi.iloc[0, 0])
        if isinstance(mi, dict):
            msgs = mi.get("messages", [])
            return msgs[-1].get("content", "") if msgs else str(mi)
        return str(mi)

    def _llm(self, prompt):
        esc = prompt.replace("'", "''")
        r = self._sql("SELECT ai_query('" + LLM_ENDPOINT + "', '" + esc + "') AS r")
        return r[0][0] if r else ""

    def _call_ka(self, query):
        try:
            resp = self._w.serving_endpoints.query(
                name=KA_ENDPOINT,
                input={"messages": [{"role": "user", "content": query}]},
            )
            return str(resp.result) if hasattr(resp, "result") else str(resp)
        except Exception as e:
            return "Knowledge Assistant error: " + str(e)

    def _call_sql_agent(self, query):
        gen_prompt = (
            "Generate a Databricks SQL query for: " + query + "\n\n"
            "Tables:\n"
            "- " + FULL_SCHEMA + ".graph_nodes (node_id, label, properties)\n"
            "- " + FULL_SCHEMA + ".graph_edges (source_id, target_id, relationship_type)\n\n"
            "Return ONLY SQL."
        )
        sql_text = self._llm(gen_prompt)
        sql_clean = sql_text.strip()
        if sql_clean.startswith("```"):
            lines = sql_clean.split("\n")
            sql_clean = "\n".join(l for l in lines if not l.strip().startswith("```"))
        try:
            rows = self._sql(sql_clean)
            if not rows:
                return "No results. SQL: " + sql_clean
            return "\n".join(str(r) for r in rows[:25]) + "\nSQL: " + sql_clean
        except Exception as e:
            return "SQL error: " + str(e)

    def _parse_route(self, text, fallback):
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                p = json.loads(text[start:end])
                if "tools" in p:
                    return p
        except Exception:
            pass
        return {"tools": ["A", "B"], "query_a": fallback, "query_b": fallback}

    def _sql(self, q):
        import time as t
        r = self._w.statement_execution.execute_statement(
            warehouse_id=self._wh_id, statement=q.strip(), wait_timeout="50s")
        while r.status and r.status.state in (self._ss.PENDING, self._ss.RUNNING):
            t.sleep(1)
            r = self._w.statement_execution.get_statement(r.statement_id)
        if r.status and r.status.state == self._ss.FAILED:
            raise RuntimeError("SQL failed: " + str(r.status.error.message))
        return r.result.data_array if r.result and r.result.data_array else []


supervisor_model_name = FULL_SCHEMA + ".insurance_supervisor_agent"

with mlflow.start_run(run_name="supervisor_agent"):
    info = mlflow.pyfunc.log_model(
        artifact_path="supervisor_agent",
        python_model=SupervisorAgent(),
        registered_model_name=supervisor_model_name,
        signature=AGENT_SIGNATURE,
        pip_requirements=["databricks-sdk", "mlflow", "databricks-agents"],
    )
    print("Supervisor logged:", info.model_uri)

print("Supervisor registered:", supervisor_model_name)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Deploy Supervisor Agent

# COMMAND ----------

deployment = agents.deploy(
    model_name=supervisor_model_name,
    model_version=1,
)
print("Deployed! Endpoint:", deployment.endpoint_name)
print("Query URL:", deployment.endpoint_url)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Test the Supervisor

# COMMAND ----------

import time
print("Waiting 60s for endpoint to warm up...")
time.sleep(60)

test_resp = w.serving_endpoints.query(
    name=deployment.endpoint_name,
    input={"messages": [{"role": "user", "content": "What are the total limits across all cyber liability policies?"}]},
)
print("Test response:")
print(test_resp)
