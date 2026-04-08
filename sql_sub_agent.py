"""
Step 5: SQL Sub-Agent using Mosaic AI Agent Framework.
Defines a SQL agent bound to graph_nodes and graph_edges tables.
Deployed as an MLflow pyfunc model with databricks.agents.

This file is intended to run on the Databricks workspace (not locally).
"""

import json
import mlflow
from mlflow.pyfunc import PythonModel

CATALOG = "lr_serverless_aws_us_catalog"
SCHEMA = "insurance_poc"
FULL_SCHEMA = f"{CATALOG}.{SCHEMA}"

SQL_AGENT_SYSTEM_PROMPT = """You are a graph data extractor. Write and execute SQL queries on the nodes and edges tables to find entity relationships and limits. Return facts.

Available tables:
- {schema}.graph_nodes (node_id STRING, label STRING, properties STRING)
  Labels: Policy, Insured, Broker, Insurer, Limit, Deductible, Clause, Exclusion, Premium

- {schema}.graph_edges (source_id STRING, target_id STRING, relationship_type STRING)
  Relationships: PLACED_BY, ISSUED_TO, UNDERWRITTEN_BY, HAS_LIMIT, HAS_DEDUCTIBLE, CONTAINS_CLAUSE, EXCLUDES, HAS_PREMIUM

The 'properties' column is a JSON string. Use json_extract_scalar or the : operator to extract fields.

Example queries:
- Find all policies: SELECT node_id, properties:policy_number, properties:class_of_business FROM {schema}.graph_nodes WHERE label = 'Policy'
- Find limits for a policy: SELECT e.source_id, n.properties FROM {schema}.graph_edges e JOIN {schema}.graph_nodes n ON e.target_id = n.node_id WHERE e.relationship_type = 'HAS_LIMIT' AND e.source_id = 'policy_xxx'
- Find all brokers: SELECT node_id, properties:name FROM {schema}.graph_nodes WHERE label = 'Broker'

Always return factual answers based on query results. Include the SQL you executed.""".format(schema=FULL_SCHEMA)


class SQLSubAgent(PythonModel):
    """SQL Sub-Agent that queries graph tables using Databricks SQL tools."""

    def load_context(self, context):
        """Initialize the agent with SQL tools."""
        from databricks.sdk import WorkspaceClient
        self.w = WorkspaceClient()
        # Find a serverless SQL warehouse
        warehouses = list(self.w.warehouses.list())
        self.warehouse_id = None
        for wh in warehouses:
            if wh.enable_serverless_compute:
                self.warehouse_id = wh.id
                break
        if not self.warehouse_id and warehouses:
            self.warehouse_id = warehouses[0].id

    def predict(self, context, model_input, params=None):
        """Process a user query by generating and executing SQL."""
        import pandas as pd
        from databricks.sdk.service.sql import StatementState
        import time

        if isinstance(model_input, pd.DataFrame):
            messages = model_input.to_dict(orient="records")
            if messages and "messages" in messages[0]:
                messages = messages[0]["messages"]
            elif messages and "content" in messages[0]:
                pass
            else:
                messages = [{"role": "user", "content": str(model_input.iloc[0, 0])}]
        elif isinstance(model_input, dict):
            messages = model_input.get("messages", [{"role": "user", "content": str(model_input)}])
        else:
            messages = [{"role": "user", "content": str(model_input)}]

        # Build conversation for LLM
        system_msg = {"role": "system", "content": SQL_AGENT_SYSTEM_PROMPT}
        all_messages = [system_msg] + messages

        # Use ai_query to generate SQL from the question
        user_question = messages[-1].get("content", "") if messages else ""

        # Step 1: Ask the LLM to generate a SQL query
        generate_sql_prompt = f"""{SQL_AGENT_SYSTEM_PROMPT}

User question: {user_question}

Generate a SQL query to answer this question. Return ONLY the SQL query, no explanation."""

        escaped_prompt = generate_sql_prompt.replace("'", "''")
        gen_sql = f"SELECT ai_query('databricks-claude-sonnet-4-6', '{escaped_prompt}') AS sql_query"

        result = self._execute_sql(gen_sql)
        if not result:
            return {"content": "Failed to generate SQL query."}

        generated_sql = result[0][0]

        # Clean the SQL (remove markdown code blocks if present)
        sql_to_execute = generated_sql.strip()
        if sql_to_execute.startswith("```"):
            lines = sql_to_execute.split("\n")
            sql_to_execute = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        # Step 2: Execute the generated SQL
        try:
            query_result = self._execute_sql(sql_to_execute)
        except Exception as e:
            return {
                "content": f"SQL execution error: {str(e)}\nGenerated SQL: {sql_to_execute}"
            }

        # Step 3: Format the results
        if not query_result:
            answer = f"Query returned no results.\nSQL: {sql_to_execute}"
        else:
            rows = [str(row) for row in query_result[:20]]
            answer = f"Query results ({len(query_result)} rows):\n" + "\n".join(rows) + f"\n\nSQL executed: {sql_to_execute}"

        return {"content": answer}

    def _execute_sql(self, sql):
        """Execute SQL via statement execution API."""
        from databricks.sdk.service.sql import StatementState
        import time

        resp = self.w.statement_execution.execute_statement(
            warehouse_id=self.warehouse_id,
            statement=sql.strip(),
            wait_timeout="50s",
        )
        while resp.status and resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
            time.sleep(1)
            resp = self.w.statement_execution.get_statement(resp.statement_id)
        if resp.status and resp.status.state == StatementState.FAILED:
            raise RuntimeError(f"SQL failed: {resp.status.error.message}")
        if resp.result and resp.result.data_array:
            return resp.result.data_array
        return []


# ── Registration script (run on Databricks) ────────────────────────────────

def register_sql_agent():
    """Register the SQL Sub-Agent as an MLflow model."""
    mlflow.set_registry_uri("databricks-uc")

    model_name = f"{CATALOG}.{SCHEMA}.sql_sub_agent"

    with mlflow.start_run(run_name="sql_sub_agent_registration"):
        model_info = mlflow.pyfunc.log_model(
            artifact_path="sql_sub_agent",
            python_model=SQLSubAgent(),
            registered_model_name=model_name,
            pip_requirements=[
                "databricks-sdk",
                "mlflow",
            ],
        )
        print(f"Model logged: {model_info.model_uri}")

    print(f"Registered model: {model_name}")
    return model_name


if __name__ == "__main__":
    register_sql_agent()
