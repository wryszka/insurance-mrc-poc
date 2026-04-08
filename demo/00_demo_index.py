# Databricks notebook source
# MAGIC %md
# MAGIC # Insurance MRC Policy Intelligence — Demo Hub
# MAGIC
# MAGIC ## What is this?
# MAGIC
# MAGIC An end-to-end AI system that extracts structured knowledge from Lloyd's Market Reform Contracts and provides a multi-agent assistant for underwriters, brokers, and compliance teams.
# MAGIC
# MAGIC **All built on Databricks. No external services.**
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ## Demo Notebooks
# MAGIC
# MAGIC | # | Notebook | What it shows | Duration |
# MAGIC |---|---------|--------------|----------|
# MAGIC | 1 | [End-to-End Walkthrough]($./01_walkthrough) | Full pipeline: PDF → parse → graph → vector → multi-agent → app | 15 min |
# MAGIC | 2 | [Change Management]($./02_change_management) | Updating ACORD specs + adding new policy files | 10 min |
# MAGIC | 3 | [Security & Audit]($./03_security_audit) | Inference logs, lineage, access control, model governance | 10 min |
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ## Live Assets
# MAGIC
# MAGIC | Asset | Link |
# MAGIC |-------|------|
# MAGIC | **Chat App** | [insurance-mrc-assistant](https://insurance-mrc-assistant-7474659673789953.aws.databricksapps.com) |
# MAGIC | **UC Schema** | `lr_serverless_aws_us_catalog.insurance_poc` |
# MAGIC | **Graph Nodes** | `lr_serverless_aws_us_catalog.insurance_poc.graph_nodes` (105 nodes) |
# MAGIC | **Graph Edges** | `lr_serverless_aws_us_catalog.insurance_poc.graph_edges` (100 edges) |
# MAGIC | **PDF Volume** | `/Volumes/lr_serverless_aws_us_catalog/insurance_poc/raw_policies/` (5 MRCs) |
# MAGIC | **Knowledge Assistant** | `ka-04bfe483-endpoint` |
# MAGIC | **Supervisor Agent** | `agents_lr_serverless_aws_us_catalog-insurance_poc-insurance_sup` |
# MAGIC | **GitHub Repo** | [wryszka/insurance-mrc-poc](https://github.com/wryszka/insurance-mrc-poc) |
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ## Architecture
# MAGIC
# MAGIC ```
# MAGIC ┌─────────────────────────────────────────────────────────────────────┐
# MAGIC │                        DATABRICKS PLATFORM                         │
# MAGIC │                                                                     │
# MAGIC │  ┌──────────┐    ┌──────────────┐    ┌──────────────────────────┐  │
# MAGIC │  │ MRC PDFs │───▶│ ai_parse_doc │───▶│ ai_query (Llama 3.3 70B)│  │
# MAGIC │  │ UC Volume│    │ (native OCR) │    │ + ACORD Dictionary       │  │
# MAGIC │  └──────────┘    └──────────────┘    └───────────┬──────────────┘  │
# MAGIC │       │                                           │                 │
# MAGIC │       │                              ┌────────────┴────────────┐   │
# MAGIC │       │                              ▼                         ▼   │
# MAGIC │       │                    ┌───────────────┐         ┌───────────┐ │
# MAGIC │       │                    │ graph_nodes   │         │graph_edges│ │
# MAGIC │       │                    │ (Delta Table) │         │(Delta)    │ │
# MAGIC │       │                    └───────┬───────┘         └─────┬─────┘ │
# MAGIC │       │                            │                       │       │
# MAGIC │       ▼                            ▼                       ▼       │
# MAGIC │  ┌──────────────┐        ┌─────────────────────────────────────┐  │
# MAGIC │  │  Knowledge   │        │        SQL Sub-Agent                │  │
# MAGIC │  │  Assistant   │        │  (NL → SQL over graph tables)       │  │
# MAGIC │  │ (Vec Search) │        └──────────────┬──────────────────────┘  │
# MAGIC │  └──────┬───────┘                       │                         │
# MAGIC │         │            ┌──────────────────┐│                         │
# MAGIC │         └──Tool A──▶│   SUPERVISOR      │◀──Tool B───────────────┘│
# MAGIC │                     │ (Claude Sonnet)   │                          │
# MAGIC │                     └────────┬──────────┘                          │
# MAGIC │                              │                                     │
# MAGIC │                     ┌────────▼──────────┐                          │
# MAGIC │                     │  Databricks App   │                          │
# MAGIC │                     │  (Streamlit UI)   │                          │
# MAGIC │                     └───────────────────┘                          │
# MAGIC │                                                                     │
# MAGIC │  ┌─────────────────────────────────────────────────────────────┐   │
# MAGIC │  │  GOVERNANCE: UC Lineage │ Audit Logs │ Inference Logging   │   │
# MAGIC │  │  Row/Column Security │ Model Registry │ Delta Time Travel  │   │
# MAGIC │  └─────────────────────────────────────────────────────────────┘   │
# MAGIC └─────────────────────────────────────────────────────────────────────┘
# MAGIC ```
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ## Databricks Services Used
# MAGIC
# MAGIC | Service | Purpose |
# MAGIC |---------|---------|
# MAGIC | Unity Catalog | Schema, volume, table, model governance |
# MAGIC | Delta Lake | Knowledge graph storage (nodes + edges) |
# MAGIC | `ai_parse_document()` | Native PDF text extraction |
# MAGIC | `ai_query()` | LLM inference (Llama 3.3 70B) |
# MAGIC | Foundation Model API | Claude Sonnet 4.6 for agent routing |
# MAGIC | Knowledge Assistants | Vector search over raw documents |
# MAGIC | Mosaic AI Agent Framework | SQL agent + supervisor deployment |
# MAGIC | Model Serving | Agent endpoint hosting |
# MAGIC | Databricks Apps | User-facing chat interface |
# MAGIC | System Tables | Audit, lineage, inference logging |
