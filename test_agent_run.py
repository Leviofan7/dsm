import requests

url = "http://127.0.0.1:8000/agent/run"
headers = {"Authorization": "Bearer default_secret", "Content-Type": "application/json"}
payload = {
    "query": "Сделай систему логина",
    "target_agent": "auto",
    "chat_id": 12345
}
res = requests.post(url, headers=headers, json=payload)
task_id = res.json()["task_id"]

stream_url = f"http://127.0.0.1:8000/agent/task/{task_id}/stream"
with requests.get(stream_url, headers=headers, stream=True) as r:
    for line in r.iter_lines():
        if line:
            print("SSE:", line.decode("utf-8"))

