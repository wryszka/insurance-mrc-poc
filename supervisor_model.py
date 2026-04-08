"""Multi-Agent Supervisor model for Mosaic AI Agent Framework."""
import json
import mlflow
from mlflow.pyfunc import ChatModel
from mlflow.types.llm import ChatCompletionResponse, ChatMessage, ChatChoice, ChatParams

CATALOG = "lr_serverless_aws_us_catalog"
SCHEMA = "insurance_poc"
FULL_SCHEMA = CATALOG + "." + SCHEMA
LLM_ENDPOINT = "databricks-claude-sonnet-4-6"
KA_ENDPOINT = "ka-04bfe483-endpoint"

SUPERVISOR_PROMPT = (
    "You are an insurance policy assistant for a Lloyd's syndicate, specialising in Market Reform Contracts.\n\n"
    "You have access to a Knowledge Assistant that can search the raw MRC policy documents.\n"
    "For every question, query the Knowledge Assistant first to get document-level context,\n"
    "then provide a clear, well-structured answer based on what it returns.\n\n"
    "Always cite specific policy numbers, clause references, and UMRs when available.\n"
    "If the Knowledge Assistant returns no relevant information, say so clearly."
)


class SupervisorAgent(ChatModel):

    def load_context(self, context):
        from databricks.sdk import WorkspaceClient
        self._w = WorkspaceClient()

    def predict(self, context, messages: list[ChatMessage], params: ChatParams = None) -> ChatCompletionResponse:
        query = messages[-1].content if messages else ""

        # Always query KA for document context
        ka_result = self._call_ka(query)

        # Synthesize with Claude
        synth_prompt = (
            SUPERVISOR_PROMPT + "\n\n"
            "User question: " + query + "\n\n"
            "Knowledge Assistant response:\n" + ka_result + "\n\n"
            "Provide a clear, concise answer based on the above. Cite sources."
        )
        final = self._llm(synth_prompt)

        return ChatCompletionResponse(
            choices=[ChatChoice(index=0, message=ChatMessage(role="assistant", content=final))]
        )

    def _call_ka(self, query):
        try:
            resp = self._w.api_client.do(
                "POST",
                "/serving-endpoints/" + KA_ENDPOINT + "/invocations",
                body={"input": [{"role": "user", "content": query}]},
            )
            if "output" in resp:
                texts = []
                for item in resp["output"]:
                    if item.get("type") == "message":
                        for block in item.get("content", []):
                            if block.get("type") == "output_text" and block.get("text"):
                                texts.append(block["text"])
                if texts:
                    return "".join(texts)
            return str(resp)
        except Exception as e:
            return "Knowledge Assistant error: " + str(e)

    def _llm(self, prompt):
        try:
            resp = self._w.api_client.do(
                "POST",
                "/serving-endpoints/" + LLM_ENDPOINT + "/invocations",
                body={"messages": [{"role": "user", "content": prompt}]},
            )
            if "choices" in resp and resp["choices"]:
                return resp["choices"][0].get("message", {}).get("content", "")
            return str(resp)
        except Exception as e:
            return "LLM error: " + str(e)


mlflow.models.set_model(SupervisorAgent())
