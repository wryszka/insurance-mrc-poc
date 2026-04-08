"""Shared helpers for the Insurance MRC POC. All scripts import from here."""

import json
import os
import time

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        cfg = json.load(f)
    if cfg["catalog"] == "YOUR_CATALOG":
        raise RuntimeError(
            "Edit config.json and set 'catalog' to your Unity Catalog catalog name before running."
        )
    cfg["full_schema"] = cfg["catalog"] + "." + cfg["schema"]
    cfg["volume_path"] = (
        "/Volumes/" + cfg["catalog"] + "/" + cfg["schema"] + "/raw_policies"
    )
    return cfg


def save_state(key: str, value):
    """Persist a runtime value (e.g. KA endpoint name) so later steps can use it."""
    state = {}
    if os.path.exists(_STATE_PATH):
        with open(_STATE_PATH) as f:
            state = json.load(f)
    state[key] = value
    with open(_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def load_state() -> dict:
    if os.path.exists(_STATE_PATH):
        with open(_STATE_PATH) as f:
            return json.load(f)
    return {}


def get_workspace_client(cfg: dict):
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient(profile=cfg["databricks_profile"])


def get_warehouse_id(w) -> str:
    warehouses = list(w.warehouses.list())
    for wh in warehouses:
        if wh.enable_serverless_compute:
            return wh.id
    if warehouses:
        return warehouses[0].id
    raise RuntimeError("No SQL warehouse found in this workspace.")


def execute_sql(w, warehouse_id: str, sql: str):
    from databricks.sdk.service.sql import StatementState
    resp = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id, statement=sql.strip(), wait_timeout="50s"
    )
    while resp.status and resp.status.state in (
        StatementState.PENDING, StatementState.RUNNING
    ):
        time.sleep(2)
        resp = w.statement_execution.get_statement(resp.statement_id)
    if resp.status and resp.status.state == StatementState.FAILED:
        raise RuntimeError(f"SQL failed: {resp.status.error}")
    if resp.result and resp.result.data_array:
        return resp.result.data_array
    return []
