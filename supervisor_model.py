"""Multi-Agent Supervisor model for Mosaic AI Agent Framework."""
import json
import mlflow
from mlflow.pyfunc import ChatModel
from mlflow.types.llm import ChatCompletionResponse, ChatMessage, ChatChoice, ChatParams

CATALOG = "lr_serverless_aws_us_catalog"
SCHEMA = "insurance_mrc_assistant"
FULL_SCHEMA = CATALOG + "." + SCHEMA
LLM_ENDPOINT = "databricks-claude-sonnet-4-6"
KA_ENDPOINT = "ka-04bfe483-endpoint"

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


class SupervisorAgent(ChatModel):

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

        return ChatCompletionResponse(
            choices=[ChatChoice(
                index=0,
                message=ChatMessage(role="assistant", content=final),
            )]
        )

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
        sql_clean = self._strip_md(sql_text)
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


mlflow.models.set_model(SupervisorAgent())
