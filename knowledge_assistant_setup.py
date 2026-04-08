"""
Step 4: Vector Pipeline - Knowledge Assistant
Creates a Knowledge Assistant agent backed by the raw_policies UC volume.
Retrieves and stores the resulting Model Serving endpoint name.
"""

import time
import json
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.knowledgeassistants import (
    KnowledgeAssistant,
    KnowledgeSource,
    FilesSpec,
    KnowledgeAssistantState,
    KnowledgeSourceState,
)

PROFILE = "fevm-lr-serverless-aws-us"
CATALOG = "lr_serverless_aws_us_catalog"
SCHEMA = "insurance_mrc_assistant"
VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/raw_policies"

KA_DISPLAY_NAME = "Insurance MRC Policy Assistant"
KA_DESCRIPTION = "Knowledge assistant for querying Lloyd's Market Reform Contract policy documents including clause text, coverage details, and exclusions."
KA_INSTRUCTIONS = """You are an insurance policy knowledge assistant specializing in Lloyd's Market Reform Contracts (MRC v3).
When answering questions:
1. Reference specific policy numbers and UMRs when citing information.
2. Quote exact clause references (e.g., LMA5218, NMA2914) when discussing policy terms.
3. Distinguish between different coverage types (Property, PI, Marine, Cyber, D&O).
4. When discussing limits, always include the currency, amount, and basis (per occurrence vs aggregate).
5. Clearly state which exclusions apply to which policy classes."""


def main():
    w = WorkspaceClient(profile=PROFILE)
    print(f"Connected as: {w.current_user.me().user_name}")

    # ── 1. Check for existing Knowledge Assistant ───────────────────────────
    existing_kas = list(w.knowledge_assistants.list_knowledge_assistants())
    ka = None
    for existing in existing_kas:
        if existing.display_name == KA_DISPLAY_NAME:
            ka = existing
            print(f"Found existing Knowledge Assistant: {ka.name} (id={ka.id})")
            break

    # ── 2. Create Knowledge Assistant ───────────────────────────────────────
    if not ka:
        print(f"\n[1/4] Creating Knowledge Assistant: {KA_DISPLAY_NAME}")
        ka = w.knowledge_assistants.create_knowledge_assistant(
            KnowledgeAssistant(
                display_name=KA_DISPLAY_NAME,
                description=KA_DESCRIPTION,
                instructions=KA_INSTRUCTIONS,
            )
        )
        print(f"  Created: {ka.name} (id={ka.id})")
    else:
        print(f"\n[1/4] Using existing Knowledge Assistant: {ka.name}")

    # ── 3. Add Knowledge Source (UC Volume) ─────────────────────────────────
    print(f"\n[2/4] Adding knowledge source: {VOLUME_PATH}")
    existing_sources = list(w.knowledge_assistants.list_knowledge_sources(parent=ka.name))
    ks = None
    for src in existing_sources:
        if src.display_name == "MRC Policy Documents":
            ks = src
            print(f"  Found existing source: {ks.name} (state={ks.state})")
            break

    if not ks:
        ks = w.knowledge_assistants.create_knowledge_source(
            parent=ka.name,
            knowledge_source=KnowledgeSource(
                display_name="MRC Policy Documents",
                description="Lloyd's Market Reform Contract PDFs from UC volume raw_policies",
                source_type="FILES",
                files=FilesSpec(path=VOLUME_PATH),
            ),
        )
        print(f"  Created source: {ks.name} (id={ks.id})")

    # ── 4. Sync knowledge sources ───────────────────────────────────────────
    print(f"\n[3/4] Syncing knowledge sources...")
    w.knowledge_assistants.sync_knowledge_sources(name=ka.name)
    print("  Sync triggered")

    # Wait for Knowledge Assistant to be ready
    print(f"\n[4/4] Waiting for Knowledge Assistant to become ACTIVE...")
    max_wait = 600  # 10 minutes
    start = time.time()
    while time.time() - start < max_wait:
        ka_status = w.knowledge_assistants.get_knowledge_assistant(ka.name)
        state = ka_status.state
        print(f"  State: {state} (elapsed: {int(time.time() - start)}s)")

        if state == KnowledgeAssistantState.ACTIVE:
            break
        elif state == KnowledgeAssistantState.FAILED:
            print(f"  ERROR: Knowledge Assistant failed: {ka_status.error_info}")
            raise RuntimeError(f"Knowledge Assistant failed: {ka_status.error_info}")

        time.sleep(15)
    else:
        print(f"  WARNING: Timed out waiting for ACTIVE state after {max_wait}s")

    # ── 5. Get endpoint info ────────────────────────────────────────────────
    ka_final = w.knowledge_assistants.get_knowledge_assistant(ka.name)
    endpoint_name = ka_final.endpoint_name

    print(f"\n=== Step 4 Complete ===")
    print(f"  Knowledge Assistant: {ka_final.display_name}")
    print(f"  Name:               {ka_final.name}")
    print(f"  ID:                 {ka_final.id}")
    print(f"  State:              {ka_final.state}")
    print(f"  Endpoint:           {endpoint_name}")

    # Save endpoint info for later steps
    config = {
        "knowledge_assistant_name": ka_final.name,
        "knowledge_assistant_id": ka_final.id,
        "endpoint_name": endpoint_name,
        "volume_path": VOLUME_PATH,
    }
    config_path = "ka_config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"\n  Config saved to: {config_path}")

    return endpoint_name


if __name__ == "__main__":
    main()
