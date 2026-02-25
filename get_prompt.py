import mlflow
mlflow.set_tracking_uri("http://10.77.77.39:5000")
prompt = mlflow.genai.load_prompt("prompts:/growth_stage_describe/7")
print(prompt.template)
