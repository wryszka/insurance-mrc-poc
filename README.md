# Insurance MRC Policy Intelligence

End-to-end AI system that extracts structured knowledge from Lloyd's Market Reform Contracts and provides a multi-agent assistant for underwriters, brokers, and compliance teams.

**Everything runs on Databricks. No external services.**

> **Disclaimer**: This application is not a Databricks product — it is a working demonstration of what can be built on the Databricks platform. All processes shown here are real and running: the data pipelines, quality checks, AI agents, model governance, and approval workflows all execute on Databricks infrastructure using Declarative Pipelines, Unity Catalog, Foundation Model API, and Databricks Apps.
>
> The data is synthetic. The regulatory templates, actuarial logic, and AI agent prompts are illustrative and should not be relied upon for actual regulatory submissions.
>
> The source code is provided as-is for demonstration and learning purposes — not for production use.

## Quick Start

### Prerequisites

- A Databricks workspace with:
  - Unity Catalog enabled
  - A catalog you have `CREATE SCHEMA` permission on (you do **not** need to create a new catalog)
  - A serverless SQL warehouse (or any running warehouse)
  - Foundation Model API access (Llama 3.3 70B + Claude Sonnet 4.6)
- Local machine with:
  - Python 3.10+
  - `databricks-sdk` installed (`pip install databricks-sdk`)
  - Databricks CLI authenticated (`databricks auth login`)

### Deploy in 3 steps

```bash
# 1. Clone the repo
git clone https://github.com/wryszka/insurance-mrc-poc.git
cd insurance-mrc-poc

# 2. Edit config.json — set your catalog and profile
#    This is the ONLY file you need to change.
vi config.json

# 3. Run the deployment
python deploy_all.py
```

### config.json — what to set

```json
{
  "catalog": "my_catalog",          // REQUIRED: your Unity Catalog catalog name
  "schema": "insurance_mrc_assistant",        // schema name (created automatically)
  "databricks_profile": "DEFAULT",  // your databricks CLI profile name
  "llm_extraction": "databricks-meta-llama-3-3-70b-instruct",  // extraction LLM
  "llm_agent": "databricks-claude-sonnet-4-6",                  // agent LLM
  "app_name": "insurance-mrc-assistant"                          // Databricks App name
}
```

**Important**: Set `catalog` to a catalog you already have access to. The script will create a schema inside it — it will **not** attempt to create a catalog.

If you're unsure which catalog to use, run:
```bash
databricks catalogs list --profile YOUR_PROFILE
```

### What gets deployed

Everything is created inside `<your_catalog>.insurance_mrc_assistant`:

| Resource | Name | Type |
|----------|------|------|
| Schema | `insurance_mrc_assistant` | Unity Catalog schema |
| Volume | `raw_policies` | Managed volume (5 MRC PDFs) |
| Table | `graph_nodes` | Delta table (entities) |
| Table | `graph_edges` | Delta table (relationships) |
| Model | `insurance_supervisor_agent` | UC registered model |
| Endpoint | `agents_<catalog>-<schema>-insurance_sup` | Model serving (supervisor) |
| Endpoint | `ka-<id>-endpoint` | Knowledge Assistant |
| Genie Room | `Insurance MRC Graph Explorer` | AI/BI Genie (NL-to-SQL) |
| App | `insurance-mrc-assistant` | Databricks App (Streamlit) |

### What gets created in your workspace

```
/Users/<you>/insurance_mrc_poc/
├── demo/
│   ├── 00_demo_index            ← START HERE for demos
│   ├── 01_walkthrough           ← End-to-end pipeline
│   ├── 02_change_management     ← ACORD updates + new files
│   ├── 03_security_audit        ← Governance & compliance
│   └── 04_performance_architecture ← Latency analysis & graph DB comparison
├── app/                         ← Streamlit app source
├── supervisor_model.py          ← Supervisor agent (KA + Claude synthesis)
├── deploy_agents                ← Agent registration notebook
└── acord_dictionary.json        ← ACORD ontology
```

## Architecture

```
MRC PDFs (UC Volume)
    │
    ├──▶ ai_parse_document() ──▶ ai_query(Llama 3.3 70B) ──▶ graph_nodes + graph_edges
    │                                                          (Delta Tables)
    │                                                               │
    │                                                               ▼
    │                                                     Genie Room (AI/BI)
    │                                                     "NL → SQL over graph"
    │                                                               │
    └──▶ Knowledge Assistant (Vector Search)                        │
         "Semantic search over raw PDFs"                            │
                    │                                               │
                    └───▶ SUPERVISOR (Claude) ◀──────── App ────────┘
                          (KA + synthesis)     (routes to Genie directly)
                                │
                                ▼
                          Databricks App
                          (Streamlit UI)
```

## Demo Flow

### 1. App Interface
Show the chat app with three modes: Supervisor, Knowledge Assistant, Genie

### 2. Behind the Scenes — Governance & Traceability
Inference logs, audit trail, lineage, access control, model registry (notebook 03)

### 3. ACORD to Delta — The Extraction Pipeline
PDF parsing, LLM extraction, knowledge graph construction (notebook 01)

### 4. Agents at Work
Supervisor + KA synthesis, Genie NL-to-SQL, live queries (notebooks 01 + app)

### 5. Change Management
Update ACORD specs (add entity types), add new files (notebook 02)

## Customising for a different catalog

If your workspace uses a non-default catalog:

1. Run `databricks catalogs list` to find available catalogs
2. Set that catalog name in `config.json`
3. The script creates everything else inside a single schema

The script **never** runs `CREATE CATALOG`. It only needs `CREATE SCHEMA` on the target catalog.

## Cleanup

To remove everything:

```sql
-- Drop the schema and all contents
DROP SCHEMA <catalog>.insurance_mrc_assistant CASCADE;
```

Then manually delete:
- The serving endpoints (Workspace UI → Serving)
- The Databricks App (Workspace UI → Apps)
- The Knowledge Assistant (Workspace UI → Knowledge Assistants)
