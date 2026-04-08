"""SQL Sub-Agent model for Mosaic AI Agent Framework."""
import json
import mlflow
from mlflow.pyfunc import ChatModel
from mlflow.types.llm import ChatCompletionResponse, ChatMessage, ChatChoice, ChatParams

CATALOG = "lr_serverless_aws_us_catalog"
SCHEMA = "insurance_poc"
FULL_SCHEMA = CATALOG + "." + SCHEMA
LLM_ENDPOINT = "databricks-claude-sonnet-4-6"

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


class SQLSubAgent(ChatModel):

    def load_context(self, context):
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.sql import StatementState as SS
        self._w = WorkspaceClient()
        self._ss = SS
        whs = list(self._w.warehouses.list())
        self._wh_id = next(
            (x.id for x in whs if x.enable_serverless_compute),
            whs[0].id if whs else None,
        )

    def predict(self, context, messages: list[ChatMessage], params: ChatParams = None) -> ChatCompletionResponse:
        query = messages[-1].content if messages else ""
        prompt = SQL_AGENT_PROMPT + "\n\nUser question: " + query + "\n\nGenerate SQL only, no explanation."
        sql_text = self._llm(prompt)
        sql_clean = self._strip_md(sql_text)
        try:
            rows = self._sql(sql_clean)
            if not rows:
                content = "No results.\nSQL: " + sql_clean
            else:
                display = "\n".join(str(r) for r in rows[:25])
                content = "Results (" + str(len(rows)) + " rows):\n" + display + "\n\nSQL: " + sql_clean
        except Exception as e:
            content = "SQL error: " + str(e) + "\nSQL: " + sql_clean

        return ChatCompletionResponse(
            choices=[ChatChoice(
                index=0,
                message=ChatMessage(role="assistant", content=content),
            )]
        )

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
            warehouse_id=self._wh_id, statement=q.strip(), wait_timeout="50s"
        )
        while r.status and r.status.state in (self._ss.PENDING, self._ss.RUNNING):
            t.sleep(1)
            r = self._w.statement_execution.get_statement(r.statement_id)
        if r.status and r.status.state == self._ss.FAILED:
            raise RuntimeError("SQL failed: " + str(r.status.error.message))
        return r.result.data_array if r.result and r.result.data_array else []


mlflow.models.set_model(SQLSubAgent())
