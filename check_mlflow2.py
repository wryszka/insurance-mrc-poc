# Databricks notebook source
# COMMAND ----------

# MAGIC %pip install databricks-agents mlflow databricks-sdk --quiet

# COMMAND ----------

# MAGIC %restart_python

# COMMAND ----------

import mlflow
print("MLflow version:", mlflow.__version__)

# Find ChatModel and its return types
from mlflow.pyfunc import ChatModel
import inspect

# Get the predict return type hint
hints = ChatModel.predict.__annotations__ if hasattr(ChatModel.predict, '__annotations__') else {}
print("predict annotations:", hints)
print("predict signature:", inspect.signature(ChatModel.predict))

# Try to find ChatResponse
search_modules = [
    "mlflow.types.llm",
    "mlflow.types.chat",
    "mlflow.pyfunc.model",
    "mlflow.pyfunc",
    "mlflow.models",
    "mlflow.models.rag_signatures",
]
for m in search_modules:
    try:
        mod = __import__(m, fromlist=["*"])
        names = [n for n in dir(mod) if "Chat" in n or "chat" in n]
        if names:
            print(f"  {m}: {names}")
    except Exception as e:
        print(f"  {m}: ERROR {e}")

# Try direct import of what the docs say
try:
    from mlflow.types.llm import ChatResponse, ChatMessage, ChatChoice
    print("\nSUCCESS: mlflow.types.llm imports work")
except ImportError:
    try:
        from mlflow.types.chat import ChatResponse, ChatMessage, ChatChoice
        print("\nSUCCESS: mlflow.types.chat imports work")
    except ImportError:
        print("\nFAILED: Cannot find ChatResponse")

# Check databricks-agents
import databricks.agents as da
print("\ndatabricks-agents version:", getattr(da, '__version__', dir(da)))

# IMPORTANT - save output as notebook result
dbutils.notebook.exit(str(mlflow.__version__))
