"""Insurance MRC Policy Assistant - Databricks App"""

import os
import json
import streamlit as st
from databricks.sdk import WorkspaceClient

SERVING_ENDPOINT = os.environ.get(
    "SERVING_ENDPOINT",
    "agents_lr_serverless_aws_us_catalog-insurance_mrc_assistant-insurance_sup",
)
KA_ENDPOINT = os.environ.get("KA_ENDPOINT", "ka-04bfe483-endpoint")

st.set_page_config(
    page_title="Insurance MRC Assistant",
    page_icon=":shield:",
    layout="wide",
)


@st.cache_resource
def get_client():
    return WorkspaceClient()


def query_supervisor(client: WorkspaceClient, question: str) -> str:
    """Query the multi-agent supervisor endpoint (ChatCompletion format)."""
    resp = client.api_client.do(
        "POST",
        f"/serving-endpoints/{SERVING_ENDPOINT}/invocations",
        body={"messages": [{"role": "user", "content": question}]},
    )
    if "choices" in resp and resp["choices"]:
        return resp["choices"][0].get("message", {}).get("content", str(resp))
    return str(resp)


def query_knowledge_assistant(client: WorkspaceClient, question: str) -> str:
    """Query the Knowledge Assistant (uses 'input' field, returns 'output' array)."""
    resp = client.api_client.do(
        "POST",
        f"/serving-endpoints/{KA_ENDPOINT}/invocations",
        body={"input": [{"role": "user", "content": question}]},
    )
    # KA returns output array with message objects containing content blocks
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


# ── Sidebar ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("Insurance MRC Assistant")
    st.markdown("**Lloyd's Market Reform Contract Analyser**")
    st.divider()

    mode = st.radio(
        "Query Mode",
        ["Supervisor (Auto-Route)", "Knowledge Assistant (Documents)", "SQL Agent (Graph Data)"],
        index=0,
    )

    st.divider()
    st.markdown("### Sample Questions")

    samples = {
        "Supervisor (Auto-Route)": [
            "What are the total limits across all cyber liability policies?",
            "Which broker placed the marine cargo policy and what exclusions apply?",
            "Compare deductibles across all 5 policies",
            "What does the LMA5218 sanction clause say?",
        ],
        "Knowledge Assistant (Documents)": [
            "What exclusions apply to the property damage policy?",
            "Describe the cyber liability coverage sections",
            "What are the terms for the D&O Side A coverage?",
        ],
        "SQL Agent (Graph Data)": [
            "List all policies with their brokers",
            "What is the total premium across all policies?",
            "Which syndicates underwrite more than one policy?",
            "Find all exclusions related to cyber",
        ],
    }

    for sample in samples.get(mode, []):
        if st.button(sample, key=sample, use_container_width=True):
            st.session_state["prefill"] = sample

    st.divider()
    st.caption("Powered by Databricks Mosaic AI Agent Framework")
    st.caption("Foundation Model: Claude Sonnet 4.6")

# ── Landing / About Tab ─────────────────────────────────────────────────────

tab_chat, tab_about = st.tabs(["Chat", "About"])

with tab_about:
    st.header("About this demo")
    st.markdown(
        "This application is not a Databricks product — it is a working "
        "demonstration of what can be built on the Databricks platform. "
        "All processes shown here are real and running: the data pipelines, "
        "quality checks, AI agents, model governance, and approval workflows "
        "all execute on Databricks infrastructure using Declarative Pipelines, "
        "Unity Catalog, Foundation Model API, and Databricks Apps."
    )
    st.markdown(
        "**The data is synthetic.** The regulatory templates, actuarial logic, "
        "and AI agent prompts are illustrative and should not be relied upon "
        "for actual regulatory submissions."
    )
    st.markdown(
        "The source code is available on "
        "[GitHub](https://github.com/wryszka/insurance-mrc-poc) and can be "
        "deployed to any Databricks workspace. It is provided as-is for "
        "demonstration and learning purposes — not for production use."
    )

    st.divider()
    st.subheader("Architecture")
    st.markdown(
        "The system uses a **multi-agent architecture**:\n\n"
        "- **Knowledge Assistant** — vector search over raw MRC PDF documents\n"
        "- **SQL Agent** — natural language to SQL over the knowledge graph (Delta tables)\n"
        "- **Supervisor Agent** (Claude Sonnet 4.6) — routes queries to the right tool and synthesises answers\n\n"
        "| Databricks Service | Role |\n"
        "|---|---|\n"
        "| Unity Catalog | Schema, volume, table, model governance |\n"
        "| `ai_parse_document()` | Native PDF text extraction |\n"
        "| `ai_query()` + Llama 3.3 70B | Graph entity/relationship extraction |\n"
        "| Knowledge Assistants | Vector search over raw documents |\n"
        "| Mosaic AI Agent Framework | SQL agent + supervisor deployment |\n"
        "| Foundation Model API | Claude Sonnet 4.6 for agent routing |\n"
        "| Databricks Apps | This UI |\n"
    )

# ── Main Chat Content ──────────────────────────────────────────────────────

with tab_chat:
    st.header("Insurance MRC Policy Assistant")
    st.markdown(
        "Ask questions about Lloyd's Market Reform Contracts. "
        "The system uses a **multi-agent architecture** with a Knowledge Assistant "
        "for document search and a SQL Agent for structured graph queries."
    )

    # Chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Display chat history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Handle prefilled question from sidebar
    prefill = st.session_state.pop("prefill", None)

    # Chat input
    prompt = st.chat_input("Ask about your insurance policies...") or prefill

    if prompt:
        # Show user message
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Get response
        with st.chat_message("assistant"):
            with st.spinner("Analysing..."):
                try:
                    client = get_client()

                    if "Knowledge Assistant" in mode:
                        response = query_knowledge_assistant(client, prompt)
                    elif "SQL Agent" in mode:
                        response = query_supervisor(
                            client,
                            "Using the SQL agent only, answer: " + prompt,
                        )
                    else:
                        response = query_supervisor(client, prompt)

                    st.markdown(response)
                    st.session_state.messages.append(
                        {"role": "assistant", "content": response}
                    )
                except Exception as e:
                    error_msg = f"Error: {str(e)}"
                    st.error(error_msg)
                    st.session_state.messages.append(
                        {"role": "assistant", "content": error_msg}
                    )

    # Footer
    st.divider()
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Policies Indexed", "5")
    with col2:
        st.metric("Graph Nodes", "105")
    with col3:
        st.metric("Graph Edges", "100")
