#!/usr/bin/env python3
"""
Master deployment script — runs locally with databricks-sdk.
Deploys the full Insurance MRC POC into any Databricks workspace.

Prerequisites:
  1. Edit config.json (set catalog name and profile)
  2. pip install databricks-sdk
  3. databricks auth login --host <your-workspace> --profile <profile>
  4. python deploy_all.py
"""

import json
import os
import sys
import glob
import time
import base64

from lib import load_config, save_state, load_state, get_workspace_client, get_warehouse_id, execute_sql

ROOT = os.path.dirname(os.path.abspath(__file__))


def step(n, title):
    print(f"\n{'='*60}")
    print(f"  STEP {n}: {title}")
    print(f"{'='*60}")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 0 — Validate config
# ─────────────────────────────────────────────────────────────────────────────
def step0_validate():
    step(0, "Validate configuration")
    cfg = load_config()
    w = get_workspace_client(cfg)
    me = w.current_user.me().user_name
    print(f"  Profile:  {cfg['databricks_profile']}")
    print(f"  User:     {me}")
    print(f"  Catalog:  {cfg['catalog']}")
    print(f"  Schema:   {cfg['schema']}")
    print(f"  Target:   {cfg['full_schema']}")

    wh_id = get_warehouse_id(w)
    print(f"  Warehouse: {wh_id}")
    save_state("warehouse_id", wh_id)
    save_state("user", me)
    return cfg, w, wh_id


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Generate PDFs
# ─────────────────────────────────────────────────────────────────────────────
def step1_generate_pdfs():
    step(1, "Generate mock MRC PDFs")
    from generate_mrc_pdfs import POLICIES, generate_pdf, OUTPUT_DIR
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for p in POLICIES:
        path = generate_pdf(p)
        print(f"  {os.path.basename(path)} ({os.path.getsize(path)/1024:.1f} KB)")
    print(f"  {len(POLICIES)} PDFs generated")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Provision schema, volume, tables, upload PDFs
# ─────────────────────────────────────────────────────────────────────────────
def step2_provision(cfg, w, wh_id):
    step(2, "Provision UC schema, volume, tables & upload PDFs")
    cat, sch, fs = cfg["catalog"], cfg["schema"], cfg["full_schema"]
    vol_path = cfg["volume_path"]

    # Schema
    try:
        w.schemas.create(name=sch, catalog_name=cat, comment="Insurance MRC POC")
        print(f"  Created schema: {fs}")
    except Exception as e:
        if "already exists" in str(e).lower():
            print(f"  Schema exists: {fs}")
        else:
            raise

    # Volume
    from databricks.sdk.service.catalog import VolumeType
    try:
        w.volumes.create(catalog_name=cat, schema_name=sch, name="raw_policies",
                         volume_type=VolumeType.MANAGED, comment="Raw MRC policy PDFs")
        print(f"  Created volume: {fs}.raw_policies")
    except Exception as e:
        if "already exists" in str(e).lower():
            print(f"  Volume exists: {fs}.raw_policies")
        else:
            raise

    # Upload PDFs
    pdf_dir = os.path.join(ROOT, "output")
    pdfs = sorted(glob.glob(os.path.join(pdf_dir, "*.pdf")))
    for pdf_path in pdfs:
        fname = os.path.basename(pdf_path)
        with open(pdf_path, "rb") as f:
            w.files.upload(vol_path + "/" + fname, f, overwrite=True)
        print(f"  Uploaded: {fname}")

    # Verify
    uploaded = list(w.files.list_directory_contents(vol_path))
    print(f"  {len(uploaded)} files in volume")

    # Tables
    execute_sql(w, wh_id, f"""
        CREATE TABLE IF NOT EXISTS {fs}.graph_nodes (
            node_id STRING NOT NULL, label STRING NOT NULL, properties STRING
        ) USING DELTA COMMENT 'Graph nodes from MRC documents'
    """)
    execute_sql(w, wh_id, f"""
        CREATE TABLE IF NOT EXISTS {fs}.graph_edges (
            source_id STRING NOT NULL, target_id STRING NOT NULL, relationship_type STRING NOT NULL
        ) USING DELTA COMMENT 'Graph edges from MRC documents'
    """)
    print(f"  Tables created: graph_nodes, graph_edges")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Graph extraction
# ─────────────────────────────────────────────────────────────────────────────
def step3_extract_graph(cfg, w, wh_id):
    step(3, "Graph extraction pipeline")
    fs = cfg["full_schema"]
    vol_path = cfg["volume_path"]
    llm = cfg["llm_extraction"]

    with open(os.path.join(ROOT, "acord_dictionary.json")) as f:
        acord = json.load(f)

    files = [f.name for f in w.files.list_directory_contents(vol_path) if f.name.endswith(".pdf")]
    print(f"  {len(files)} PDFs to process")

    execute_sql(w, wh_id, f"TRUNCATE TABLE {fs}.graph_nodes")
    execute_sql(w, wh_id, f"TRUNCATE TABLE {fs}.graph_edges")

    entity_defs = json.dumps(acord["entities"], indent=2)
    rel_defs = json.dumps(acord["relationships"], indent=2)
    total_n, total_e = 0, 0

    for pdf_name in files:
        print(f"  Processing: {pdf_name}")

        # Parse
        result = execute_sql(w, wh_id, f"""
            SELECT cast(ai_parse_document(content) AS STRING)
            FROM read_files('{vol_path}/{pdf_name}')
        """)
        if not result or not result[0][0]:
            print(f"    SKIP: no text extracted")
            continue
        parsed = json.loads(result[0][0])
        elements = parsed.get("document", {}).get("elements", [])
        text = "\n".join(el.get("content", "") for el in elements if el.get("content"))

        # Extract
        prompt = (
            "You are an insurance document graph extractor. Extract entities and relationships.\n\n"
            f"ENTITIES:\n{entity_defs}\n\nRELATIONSHIPS:\n{rel_defs}\n\n"
            "RULES: node_id format <type>_<short_id>. Return ONLY JSON: "
            '{"nodes":[{"node_id":"...","label":"...","properties":{...}}],'
            '"edges":[{"source_id":"...","target_id":"...","relationship_type":"..."}]}\n\n'
            f"DOCUMENT:\n{text}"
        )
        escaped = prompt.replace("'", "''")
        result = execute_sql(w, wh_id, f"SELECT ai_query('{llm}', '{escaped}')")
        if not result or not result[0][0]:
            print(f"    SKIP: no LLM response")
            continue
        raw = result[0][0]

        # Parse JSON
        graph = None
        for attempt in [raw.strip(), raw[raw.find("{"):raw.rfind("}")+1]]:
            try:
                graph = json.loads(attempt)
                break
            except (json.JSONDecodeError, ValueError):
                continue
        if not graph:
            if "```" in raw:
                for part in raw.split("```"):
                    cleaned = part.strip().removeprefix("json").strip()
                    try:
                        graph = json.loads(cleaned)
                        break
                    except:
                        continue
        if not graph:
            print(f"    SKIP: could not parse JSON")
            continue

        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        # Insert
        if nodes:
            vals = ", ".join(
                f"('{n['node_id'].replace(chr(39),'')}',"
                f"'{n['label'].replace(chr(39),'')}',"
                f"'{json.dumps(n.get('properties',{})).replace(chr(39),chr(39)+chr(39))}')"
                for n in nodes
            )
            execute_sql(w, wh_id, f"INSERT INTO {fs}.graph_nodes VALUES {vals}")
        if edges:
            vals = ", ".join(
                f"('{e['source_id'].replace(chr(39),'')}',"
                f"'{e['target_id'].replace(chr(39),'')}',"
                f"'{e['relationship_type'].replace(chr(39),'')}')"
                for e in edges
            )
            execute_sql(w, wh_id, f"INSERT INTO {fs}.graph_edges VALUES {vals}")

        total_n += len(nodes)
        total_e += len(edges)
        print(f"    {len(nodes)} nodes, {len(edges)} edges")

    print(f"\n  Total: {total_n} nodes, {total_e} edges")
    save_state("total_nodes", total_n)
    save_state("total_edges", total_e)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Knowledge Assistant
# ─────────────────────────────────────────────────────────────────────────────
def step4_knowledge_assistant(cfg, w):
    step(4, "Create Knowledge Assistant")
    from databricks.sdk.service.knowledgeassistants import (
        KnowledgeAssistant, KnowledgeSource, FilesSpec, KnowledgeAssistantState
    )

    vol_path = cfg["volume_path"]
    display_name = "Insurance MRC Policy Assistant"

    # Check existing
    ka = None
    for existing in w.knowledge_assistants.list_knowledge_assistants():
        if existing.display_name == display_name:
            ka = existing
            break

    if not ka:
        ka = w.knowledge_assistants.create_knowledge_assistant(
            KnowledgeAssistant(
                display_name=display_name,
                description="Knowledge assistant for Lloyd's MRC policy documents",
                instructions="You are an insurance policy assistant. Reference policy numbers and clause IDs.",
            )
        )
        print(f"  Created: {ka.name}")
    else:
        print(f"  Exists: {ka.name}")

    # Add source
    sources = list(w.knowledge_assistants.list_knowledge_sources(parent=ka.name))
    if not sources:
        w.knowledge_assistants.create_knowledge_source(
            parent=ka.name,
            knowledge_source=KnowledgeSource(
                display_name="MRC Policy Documents",
                description="PDFs from UC volume",
                source_type="FILES",
                files=FilesSpec(path=vol_path),
            ),
        )
        print(f"  Added knowledge source: {vol_path}")
    else:
        print(f"  Source exists")

    w.knowledge_assistants.sync_knowledge_sources(name=ka.name)
    print(f"  Sync triggered")

    # Wait (max 10 min)
    start = time.time()
    while time.time() - start < 600:
        status = w.knowledge_assistants.get_knowledge_assistant(ka.name)
        if status.state == KnowledgeAssistantState.ACTIVE:
            break
        if status.state == KnowledgeAssistantState.FAILED:
            raise RuntimeError(f"KA failed: {status.error_info}")
        print(f"  {status.state} ({int(time.time()-start)}s)")
        time.sleep(15)

    ka_final = w.knowledge_assistants.get_knowledge_assistant(ka.name)
    ep = ka_final.endpoint_name
    print(f"  Knowledge Assistant ACTIVE — endpoint: {ep}")
    save_state("ka_name", ka_final.name)
    save_state("ka_endpoint", ep)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Upload agent model files & deploy notebook
# ─────────────────────────────────────────────────────────────────────────────
def step5_upload_and_deploy_agents(cfg, w):
    step(5, "Register & deploy agents")
    from databricks.sdk.service.workspace import ImportFormat, Language
    from databricks.sdk.service.jobs import SubmitTask, NotebookTask, RunLifeCycleState

    state = load_state()
    ka_ep = state.get("ka_endpoint", "UNKNOWN")
    me = state.get("user", w.current_user.me().user_name)
    fs = cfg["full_schema"]
    llm_agent = cfg["llm_agent"]

    ws_dir = f"/Users/{me}/insurance_mrc_poc"
    try:
        w.workspace.mkdirs(ws_dir)
    except:
        pass

    # Generate model files with correct config baked in
    sql_model = _render_sql_agent_model(fs, llm_agent)
    sup_model = _render_supervisor_model(fs, llm_agent, ka_ep)
    deploy_nb = _render_deploy_notebook(fs)

    for name, content, fmt, lang in [
        ("sql_agent_model.py", sql_model, ImportFormat.AUTO, None),
        ("supervisor_model.py", sup_model, ImportFormat.AUTO, None),
        ("deploy_agents", deploy_nb, ImportFormat.SOURCE, Language.PYTHON),
    ]:
        w.workspace.import_(
            path=ws_dir + "/" + name,
            content=base64.b64encode(content.encode()).decode(),
            format=fmt,
            language=lang,
            overwrite=True,
        )
        print(f"  Uploaded: {name}")

    # Run deploy notebook
    print(f"  Running deploy notebook...")
    run = w.jobs.submit(
        run_name="Insurance MRC POC — Agent Deploy",
        tasks=[SubmitTask(task_key="deploy", notebook_task=NotebookTask(
            notebook_path=ws_dir + "/deploy_agents",
        ))],
    )

    start = time.time()
    while True:
        s = w.jobs.get_run(run.run_id)
        if s.state.life_cycle_state in (RunLifeCycleState.TERMINATED, RunLifeCycleState.SKIPPED, RunLifeCycleState.INTERNAL_ERROR):
            break
        print(f"    running... ({int(time.time()-start)}s)")
        time.sleep(30)

    if s.state.result_state and str(s.state.result_state) == "SUCCESS":
        for task in s.tasks:
            try:
                out = w.jobs.get_run_output(task.run_id)
                if out.notebook_output and out.notebook_output.result:
                    ep_name = out.notebook_output.result.replace("Deployed: ", "")
                    save_state("supervisor_endpoint", ep_name)
                    print(f"  Deployed: {ep_name}")
            except:
                pass
    else:
        for task in s.tasks:
            try:
                out = w.jobs.get_run_output(task.run_id)
                if out.error:
                    print(f"  ERROR: {out.error[:500]}")
            except:
                pass
        raise RuntimeError("Agent deployment failed — check workspace notebook for details")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — Deploy Databricks App
# ─────────────────────────────────────────────────────────────────────────────
def step6_deploy_app(cfg, w):
    step(6, "Deploy Databricks App")
    from databricks.sdk.service.workspace import ImportFormat
    from databricks.sdk.service.apps import (
        App, AppResource, AppDeployment,
        AppResourceServingEndpoint, AppResourceServingEndpointServingEndpointPermission,
        AppResourceSqlWarehouse, AppResourceSqlWarehouseSqlWarehousePermission,
    )

    state = load_state()
    sup_ep = state.get("supervisor_endpoint", "")
    ka_ep = state.get("ka_endpoint", "")
    wh_id = state.get("warehouse_id", "")
    me = state.get("user", w.current_user.me().user_name)
    app_name = cfg["app_name"]

    # Upload app files
    ws_app = f"/Users/{me}/insurance_mrc_poc/app"
    try:
        w.workspace.mkdirs(ws_app)
    except:
        pass

    app_py = _render_app_py(sup_ep, ka_ep)
    app_yaml = _render_app_yaml(sup_ep, ka_ep, wh_id)
    reqs = "streamlit==1.45.1\ndatabricks-sdk>=0.40.0\n"

    for name, content in [("app.py", app_py), ("app.yaml", app_yaml), ("requirements.txt", reqs)]:
        w.workspace.import_(
            path=ws_app + "/" + name,
            content=base64.b64encode(content.encode()).decode(),
            format=ImportFormat.AUTO,
            overwrite=True,
        )
        print(f"  Uploaded: {name}")

    # Create app
    existing = None
    for a in w.apps.list():
        if a.name == app_name:
            existing = a
            break

    if not existing:
        resources = [
            AppResource(name="sup-ep", serving_endpoint=AppResourceServingEndpoint(
                name=sup_ep, permission=AppResourceServingEndpointServingEndpointPermission.CAN_QUERY)),
        ]
        if ka_ep:
            resources.append(AppResource(name="ka-ep", serving_endpoint=AppResourceServingEndpoint(
                name=ka_ep, permission=AppResourceServingEndpointServingEndpointPermission.CAN_QUERY)))
        if wh_id:
            resources.append(AppResource(name="wh", sql_warehouse=AppResourceSqlWarehouse(
                id=wh_id, permission=AppResourceSqlWarehouseSqlWarehousePermission.CAN_USE)))

        app = w.apps.create_and_wait(App(name=app_name, description="Insurance MRC Policy Assistant", resources=resources))
        print(f"  Created app: {app.name}")
    else:
        print(f"  App exists: {existing.name}")

    # Deploy
    dep = w.apps.deploy(
        app_name=app_name,
        app_deployment=AppDeployment(source_code_path="/Workspace" + ws_app),
    ).result()
    print(f"  Deployed: {dep.status}")

    # Get URL
    app = w.apps.get(app_name)
    save_state("app_url", app.url)
    print(f"  URL: {app.url}")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 7 — Upload demo notebooks
# ─────────────────────────────────────────────────────────────────────────────
def step7_upload_demos(cfg, w):
    step(7, "Upload demo notebooks")
    from databricks.sdk.service.workspace import ImportFormat, Language

    state = load_state()
    me = state.get("user", w.current_user.me().user_name)
    demo_dir = f"/Users/{me}/insurance_mrc_poc/demo"
    try:
        w.workspace.mkdirs(demo_dir)
    except:
        pass

    # Also upload acord_dictionary.json to workspace root for notebooks
    ws_root = f"/Users/{me}/insurance_mrc_poc"
    with open(os.path.join(ROOT, "acord_dictionary.json"), "r") as f:
        content = f.read()
    w.workspace.import_(
        path=ws_root + "/acord_dictionary.json",
        content=base64.b64encode(content.encode()).decode(),
        format=ImportFormat.AUTO,
        overwrite=True,
    )

    local_demo = os.path.join(ROOT, "demo")
    for fname in sorted(os.listdir(local_demo)):
        if fname.endswith(".py"):
            with open(os.path.join(local_demo, fname)) as f:
                content = f.read()
            # Replace hardcoded references with this deployment's values
            content = content.replace("lr_serverless_aws_us_catalog.insurance_poc", cfg["full_schema"])
            content = content.replace("lr_serverless_aws_us_catalog", cfg["catalog"])
            content = content.replace("insurance_poc", cfg["schema"])
            content = content.replace("ka-04bfe483-endpoint", state.get("ka_endpoint", ""))
            content = content.replace(
                "agents_lr_serverless_aws_us_catalog-insurance_poc-insurance_sup",
                state.get("supervisor_endpoint", "")
            )
            content = content.replace(
                "https://insurance-mrc-assistant-7474659673789953.aws.databricksapps.com",
                state.get("app_url", "")
            )
            content = content.replace(
                "knowledge-assistants/04bfe483-92eb-42c9-970c-d796f99028a1",
                state.get("ka_name", "")
            )

            nb_name = fname.replace(".py", "")
            w.workspace.import_(
                path=demo_dir + "/" + nb_name,
                content=base64.b64encode(content.encode()).decode(),
                format=ImportFormat.SOURCE,
                language=Language.PYTHON,
                overwrite=True,
            )
            print(f"  Uploaded: {nb_name}")

    print(f"\n  Demo hub: {demo_dir}/00_demo_index")


# ─────────────────────────────────────────────────────────────────────────────
# Template renderers
# ─────────────────────────────────────────────────────────────────────────────

def _render_sql_agent_model(full_schema, llm_endpoint):
    with open(os.path.join(ROOT, "sql_agent_model.py")) as f:
        code = f.read()
    code = code.replace('CATALOG + "." + SCHEMA', f'"{full_schema}"')
    code = code.replace('CATALOG = "lr_serverless_aws_us_catalog"', f'CATALOG = "{full_schema.split(".")[0]}"')
    code = code.replace('SCHEMA = "insurance_poc"', f'SCHEMA = "{full_schema.split(".")[1]}"')
    code = code.replace("databricks-claude-sonnet-4-6", llm_endpoint)
    return code


def _render_supervisor_model(full_schema, llm_endpoint, ka_endpoint):
    with open(os.path.join(ROOT, "supervisor_model.py")) as f:
        code = f.read()
    code = code.replace('CATALOG = "lr_serverless_aws_us_catalog"', f'CATALOG = "{full_schema.split(".")[0]}"')
    code = code.replace('SCHEMA = "insurance_poc"', f'SCHEMA = "{full_schema.split(".")[1]}"')
    code = code.replace("databricks-claude-sonnet-4-6", llm_endpoint)
    code = code.replace("ka-04bfe483-endpoint", ka_endpoint)
    return code


def _render_deploy_notebook(full_schema):
    with open(os.path.join(ROOT, "deploy_agents.py")) as f:
        code = f.read()
    code = code.replace('CATALOG = "lr_serverless_aws_us_catalog"', f'CATALOG = "{full_schema.split(".")[0]}"')
    code = code.replace('SCHEMA = "insurance_poc"', f'SCHEMA = "{full_schema.split(".")[1]}"')
    return code


def _render_app_py(supervisor_ep, ka_ep):
    with open(os.path.join(ROOT, "app", "app.py")) as f:
        code = f.read()
    code = code.replace("agents_lr_serverless_aws_us_catalog-insurance_poc-insurance_sup", supervisor_ep)
    code = code.replace("ka-04bfe483-endpoint", ka_ep)
    return code


def _render_app_yaml(supervisor_ep, ka_ep, wh_id):
    return f'''command:
  - streamlit
  - run
  - app.py
  - --server.port
  - "8000"
  - --server.address
  - "0.0.0.0"

env:
  - name: SERVING_ENDPOINT
    value: "{supervisor_ep}"
  - name: KA_ENDPOINT
    value: "{ka_ep}"

resources:
  - name: serving-endpoint
    serving_endpoint:
      name: "{supervisor_ep}"
      permission: CAN_QUERY
  - name: ka-endpoint
    serving_endpoint:
      name: "{ka_ep}"
      permission: CAN_QUERY
  - name: sql-warehouse
    sql_warehouse:
      id: "{wh_id}"
      permission: CAN_USE
'''


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("Insurance MRC POC — Full Deployment")
    print("=" * 60)

    cfg, w, wh_id = step0_validate()
    step1_generate_pdfs()
    step2_provision(cfg, w, wh_id)
    step3_extract_graph(cfg, w, wh_id)
    step4_knowledge_assistant(cfg, w)
    step5_upload_and_deploy_agents(cfg, w)
    step6_deploy_app(cfg, w)
    step7_upload_demos(cfg, w)

    state = load_state()
    print("\n" + "=" * 60)
    print("  DEPLOYMENT COMPLETE")
    print("=" * 60)
    print(f"  Schema:      {cfg['full_schema']}")
    print(f"  Graph:       {state.get('total_nodes',0)} nodes, {state.get('total_edges',0)} edges")
    print(f"  KA Endpoint: {state.get('ka_endpoint','')}")
    print(f"  Supervisor:  {state.get('supervisor_endpoint','')}")
    print(f"  App URL:     {state.get('app_url','')}")
    print(f"  Demo Hub:    /Users/{state.get('user','')}/insurance_mrc_poc/demo/00_demo_index")
    print()


if __name__ == "__main__":
    main()
