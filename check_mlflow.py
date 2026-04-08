# Databricks notebook source
# COMMAND ----------

# MAGIC %pip install databricks-agents mlflow databricks-sdk --quiet

# COMMAND ----------

# MAGIC %restart_python

# COMMAND ----------

import mlflow
print("MLflow version:", mlflow.__version__)

try:
    from mlflow.pyfunc import ChatModel
    print("ChatModel: available")
    import inspect
    sig = inspect.signature(ChatModel.predict)
    print("  predict signature:", sig)
except ImportError as e:
    print("ChatModel: NOT available -", e)

for mod_path in ["mlflow.types.llm", "mlflow.types.chat", "mlflow.pyfunc", "mlflow.models"]:
    try:
        mod = __import__(mod_path, fromlist=["ChatResponse", "ChatMessage", "ChatChoice", "ChatCompletionResponse"])
        found = [x for x in ["ChatResponse", "ChatMessage", "ChatChoice", "ChatCompletionResponse"] if hasattr(mod, x)]
        if found:
            print(f"  {mod_path}: {found}")
    except Exception:
        pass

try:
    from mlflow.models.rag_signatures import ChatCompletionRequest
    print("rag_signatures.ChatCompletionRequest: available")
except Exception as e:
    print(f"rag_signatures: {e}")

try:
    import databricks.agents
    print("databricks.agents:", getattr(databricks.agents, '__version__', 'unknown'))
except ImportError as e:
    print(f"databricks.agents: {e}")
