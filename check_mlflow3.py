# Databricks notebook source
# COMMAND ----------

# MAGIC %pip install databricks-agents mlflow databricks-sdk --quiet

# COMMAND ----------

# MAGIC %restart_python

# COMMAND ----------

import mlflow
results = []
results.append(f"MLflow: {mlflow.__version__}")

# Check ChatModel predict signature
from mlflow.pyfunc import ChatModel
import inspect
sig = inspect.signature(ChatModel.predict)
results.append(f"predict sig: {sig}")
hints = getattr(ChatModel.predict, '__annotations__', {})
results.append(f"annotations: {hints}")

# Get the return type
ret_type = hints.get('return', None)
if ret_type:
    results.append(f"return type: {ret_type}")
    results.append(f"return module: {getattr(ret_type, '__module__', 'unknown')}")

# Search for Chat types in mlflow 3.x
for m in ["mlflow.types.llm", "mlflow.types.chat", "mlflow.pyfunc.model"]:
    try:
        mod = __import__(m, fromlist=["*"])
        chat_names = [n for n in dir(mod) if "Chat" in n]
        if chat_names:
            results.append(f"{m}: {chat_names}")
    except ImportError:
        results.append(f"{m}: not found")

# Try the actual ChatModel return type
try:
    # In MLflow 3.x, predict returns dict-like or specific type
    from mlflow.pyfunc import ChatModel
    # Check if there's a ChatCompletionResponse
    source = inspect.getsource(ChatModel.predict)
    results.append(f"predict source lines: {len(source.split(chr(10)))}")
    results.append(f"predict source: {source[:500]}")
except Exception as e:
    results.append(f"source error: {e}")

dbutils.notebook.exit("|||".join(results))
