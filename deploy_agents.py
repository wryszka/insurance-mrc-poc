# Databricks notebook source
# MAGIC %md
# MAGIC # Insurance MRC POC - Agent Deployment
# MAGIC Code-based model logging with MLflow 3.x for SQL Sub-Agent and Supervisor.

# COMMAND ----------

# MAGIC %pip install databricks-agents mlflow databricks-sdk --quiet

# COMMAND ----------

# MAGIC %restart_python

# COMMAND ----------

import mlflow
import databricks.agents as agents
from databricks.sdk import WorkspaceClient

mlflow.set_registry_uri("databricks-uc")

CATALOG = "lr_serverless_aws_us_catalog"
SCHEMA = "insurance_poc"
FULL_SCHEMA = CATALOG + "." + SCHEMA
WS_DIR = "/Workspace/Users/laurence.ryszka@databricks.com/insurance_mrc_poc"

w = WorkspaceClient()
print("Connected as:", w.current_user.me().user_name)
print("MLflow:", mlflow.__version__)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Register SQL Sub-Agent

# COMMAND ----------

sql_model_name = FULL_SCHEMA + ".sql_sub_agent"

with mlflow.start_run(run_name="sql_sub_agent"):
    info = mlflow.pyfunc.log_model(
        artifact_path="sql_sub_agent",
        python_model=WS_DIR + "/sql_agent_model.py",
        registered_model_name=sql_model_name,
        pip_requirements=["databricks-sdk", "mlflow"],
    )
    print("SQL Agent logged:", info.model_uri)

print("SQL Agent registered:", sql_model_name)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Register Multi-Agent Supervisor

# COMMAND ----------

supervisor_model_name = FULL_SCHEMA + ".insurance_supervisor_agent"

with mlflow.start_run(run_name="supervisor_agent"):
    info = mlflow.pyfunc.log_model(
        artifact_path="supervisor_agent",
        python_model=WS_DIR + "/supervisor_model.py",
        registered_model_name=supervisor_model_name,
        pip_requirements=["databricks-sdk", "mlflow", "databricks-agents"],
    )
    print("Supervisor logged:", info.model_uri)

print("Supervisor registered:", supervisor_model_name)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Deploy Supervisor Agent

# COMMAND ----------

# Get latest model version
from mlflow import MlflowClient
client = MlflowClient()
versions = client.search_model_versions("name = '" + supervisor_model_name + "'")
latest_version = max(int(v.version) for v in versions)
print("Deploying version:", latest_version)

deployment = agents.deploy(
    model_name=supervisor_model_name,
    model_version=latest_version,
)
print("Deployed! Endpoint:", deployment.endpoint_name)
print("Query URL:", deployment.endpoint_url)

dbutils.notebook.exit("Deployed: " + deployment.endpoint_name)
