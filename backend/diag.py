import subprocess
import time
import requests
import os

if "ANTHROPIC_API_KEY" in os.environ:
    del os.environ["ANTHROPIC_API_KEY"]

p = subprocess.Popen(["uv", "run", "uvicorn", "main:app", "--port", "8002"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

time.sleep(3) # Wait for server to start
try:
    resp = requests.post("http://127.0.0.1:8002/api/pull", json={"query": "Data Engineer"})
    print("POST Response:", resp.text)
    
    time.sleep(3) # Wait for agent task to fail or run
    
    status = requests.get("http://127.0.0.1:8002/api/status").json()
    print("STATUS:", status)
    
finally:
    p.terminate()
    stdout, _ = p.communicate()
    print("----- SERVER LOGS -----")
    print(stdout)
