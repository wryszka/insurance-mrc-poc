"""
Step 6: Multi-Agent Supervisor
Routes queries to Knowledge Assistant (Tool A) or SQL Sub-Agent (Tool B).
Uses Claude as the foundation model. Deployed via databricks.agents.deploy().

This file is intended to run on the Databricks workspace (not locally).
"""

import json
import mlflow
from mlflow.pyfunc import PythonModel

CATALOG = "lr_serverless_aws_us_catalog"
SCHEMA = "insurance_mrc_assistant"
FULL_SCHEMA = f"{CATALOG}.{SCHEMA}"

# Loaded from ka_config.json at deploy time
KA_ENDPOINT = "ka-04bfe483-endpoint"
LLM_ENDPOINT = "databricks-claude-sonnet-4-6"

SUPERVISOR_SYSTEM_PROMPT = """You are an insurance policy supervisor agent for Lloyd's Market Reform Contracts.

You have two tools to answer questions:

TOOL A - Knowledge Assistant (call for: clause text, policy wording, semantic search, unstructured content)
Use this when the question involves:
- Exact policy wording or clause text
- Understanding what a specific exclusion or clause means
- Searching for specific terms across documents
- General questions about policy coverage descriptions

TOOL B - SQL Sub-Agent (call for: limits, relationships, aggregations, structured data)
Use this when the question involves:
- Specific dollar/pound amounts, limits, deductibles, premiums
- Relationships between entities (which broker placed which policy, which syndicate underwrites what)
- Aggregations, comparisons, or calculations across policies
- Counting or listing entities (how many policies, which insurers)

ROUTING RULES:
1. For text/semantics questions -> use Tool A (Knowledge Assistant)
2. For relationship/math/structured questions -> use Tool B (SQL Agent)
3. For complex questions -> call BOTH tools and synthesize the answer
4. Always cite which tool(s) provided the information in your response

Respond with a JSON object indicating which tool(s) to call:
{{"tools": ["A"], "query": "the refined query for Tool A"}}
{{"tools": ["B"], "query": "the refined query for Tool B"}}
{{"tools": ["A", "B"], "query_a": "query for Tool A", "query_b": "query for Tool B"}}
"""


class SupervisorAgent(PythonModel):
    """Multi-Agent Supervisor routing to Knowledge Assistant and SQL Agent."""

    def load_context(self, context):
        """Initialize SDK client and sub-agent connections."""
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.sql import StatementState
        self.w = WorkspaceClient()
        self.StatementState = StatementState

        # Find SQL warehouse
        warehouses = list(self.w.warehouses.list())
        self.warehouse_id = None
        for wh in warehouses:
            if wh.enable_serverless_compute:
                self.warehouse_id = wh.id
                break
        if not self.warehouse_id and warehouses:
            self.warehouse_id = warehouses[0].id

    def predict(self, context, model_input, params=None):
        """Route query to appropriate sub-agent(s) and synthesize response."""
        import pandas as pd

        # Extract user message
        if isinstance(model_input, pd.DataFrame):
            records = model_input.to_dict(orient="records")
            if records and "messages" in records[0]:
                messages = records[0]["messages"]
                user_query = messages[-1].get("content", "") if messages else ""
            else:
                user_query = str(model_input.iloc[0, 0])
        elif isinstance(model_input, dict):
            messages = model_input.get("messages", [])
            user_query = messages[-1].get("content", "") if messages else str(model_input)
        else:
            user_query = str(model_input)

        # Step 1: Ask the supervisor LLM to route the query
        routing_prompt = f"""{SUPERVISOR_SYSTEM_PROMPT}

User question: {user_query}

Which tool(s) should handle this? Return ONLY the JSON routing object."""

        routing_result = self._call_llm(routing_prompt)
        routing = self._parse_routing(routing_result, user_query)

        # Step 2: Call the appropriate tool(s)
        tool_a_result = None
        tool_b_result = None

        if "A" in routing["tools"]:
            query_a = routing.get("query_a", routing.get("query", user_query))
            tool_a_result = self._call_knowledge_assistant(query_a)

        if "B" in routing["tools"]:
            query_b = routing.get("query_b", routing.get("query", user_query))
            tool_b_result = self._call_sql_agent(query_b)

        # Step 3: Synthesize the final answer
        synthesis_prompt = f"""You are an insurance policy expert synthesizing answers from multiple sources.

Original question: {user_query}

"""
        if tool_a_result:
            synthesis_prompt += f"""Knowledge Assistant response (unstructured document search):
{tool_a_result}

"""
        if tool_b_result:
            synthesis_prompt += f"""SQL Agent response (structured graph data):
{tool_b_result}

"""
        synthesis_prompt += """Provide a clear, comprehensive answer combining all available information.
Cite which source (Knowledge Assistant or SQL Agent) each piece of information came from.
If information conflicts, note the discrepancy."""

        final_answer = self._call_llm(synthesis_prompt)

        return {"content": final_answer}

    def _call_llm(self, prompt):
        """Call the Claude LLM via ai_query."""
        escaped = prompt.replace("'", "''")
        sql = f"SELECT ai_query('{LLM_ENDPOINT}', '{escaped}') AS response"
        result = self._execute_sql(sql)
        return result[0][0] if result else "No response from LLM."

    def _call_knowledge_assistant(self, query):
        """Call the Knowledge Assistant endpoint."""
        try:
            response = self.w.serving_endpoints.query(
                name=KA_ENDPOINT,
                input={"messages": [{"role": "user", "content": query}]},
            )
            if hasattr(response, "result"):
                return str(response.result)
            return str(response)
        except Exception as e:
            return f"Knowledge Assistant error: {str(e)}"

    def _call_sql_agent(self, query):
        """Inline SQL agent - generate and execute SQL for graph queries."""
        # Generate SQL
        gen_prompt = f"""You are a SQL expert. Generate a Databricks SQL query to answer this question about insurance policy graph data.

Tables:
- {FULL_SCHEMA}.graph_nodes (node_id STRING, label STRING, properties STRING)
  Labels: Policy, Insured, Broker, Insurer, Limit, Deductible, Clause, Exclusion, Premium
  properties is JSON - use properties:field_name syntax

- {FULL_SCHEMA}.graph_edges (source_id STRING, target_id STRING, relationship_type STRING)
  Relationships: PLACED_BY, ISSUED_TO, UNDERWRITTEN_BY, HAS_LIMIT, HAS_DEDUCTIBLE, CONTAINS_CLAUSE, EXCLUDES, HAS_PREMIUM

Question: {query}

Return ONLY the SQL query, nothing else."""

        sql_response = self._call_llm(gen_prompt)
        sql_to_run = sql_response.strip()

        # Clean markdown
        if sql_to_run.startswith("```"):
            lines = sql_to_run.split("\n")
            sql_to_run = "\n".join(
                line for line in lines
                if not line.strip().startswith("```")
            )

        try:
            result = self._execute_sql(sql_to_run)
            if not result:
                return f"Query returned no results.\nSQL: {sql_to_run}"
            rows = [str(row) for row in result[:25]]
            return f"Results ({len(result)} rows):\n" + "\n".join(rows) + f"\n\nSQL: {sql_to_run}"
        except Exception as e:
            return f"SQL error: {str(e)}\nGenerated SQL: {sql_to_run}"

    def _parse_routing(self, routing_text, fallback_query):
        """Parse the routing JSON from the LLM response."""
        try:
            # Try direct parse
            parsed = json.loads(routing_text.strip())
            if "tools" in parsed:
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

        # Try extracting JSON from response
        text = routing_text.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start:end])
                if "tools" in parsed:
                    return parsed
            except json.JSONDecodeError:
                pass

        # Default: route to both tools
        return {"tools": ["A", "B"], "query_a": fallback_query, "query_b": fallback_query}

    def _execute_sql(self, sql):
        """Execute SQL via statement execution API."""
        import time
        resp = self.w.statement_execution.execute_statement(
            warehouse_id=self.warehouse_id,
            statement=sql.strip(),
            wait_timeout="50s",
        )
        while resp.status and resp.status.state in (
            self.StatementState.PENDING,
            self.StatementState.RUNNING,
        ):
            time.sleep(1)
            resp = self.w.statement_execution.get_statement(resp.statement_id)
        if resp.status and resp.status.state == self.StatementState.FAILED:
            raise RuntimeError(f"SQL failed: {resp.status.error.message}")
        if resp.result and resp.result.data_array:
            return resp.result.data_array
        return []


# ── Registration and deployment script (run on Databricks) ──────────────────

def register_and_deploy():
    """Register the Supervisor agent and deploy it."""
    import databricks.agents as agents

    mlflow.set_registry_uri("databricks-uc")

    model_name = f"{CATALOG}.{SCHEMA}.insurance_supervisor_agent"

    with mlflow.start_run(run_name="supervisor_agent_registration"):
        model_info = mlflow.pyfunc.log_model(
            artifact_path="supervisor_agent",
            python_model=SupervisorAgent(),
            registered_model_name=model_name,
            pip_requirements=[
                "databricks-sdk",
                "mlflow",
                "databricks-agents",
            ],
        )
        print(f"Model logged: {model_info.model_uri}")

    print(f"Registered model: {model_name}")

    # Deploy the agent
    deployment = agents.deploy(
        model_name=model_name,
        model_version=1,
    )
    print(f"Deployed! Endpoint: {deployment.endpoint_name}")
    print(f"Query endpoint: {deployment.endpoint_url}")

    return deployment


if __name__ == "__main__":
    register_and_deploy()
