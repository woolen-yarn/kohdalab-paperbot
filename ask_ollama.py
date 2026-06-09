import sys
import json
import urllib.request

OLLAMA_URL = "http://10.32.145.143:11434/api/generate"
MODEL = "qwen3:8b"

prompt = sys.argv[1] if len(sys.argv) > 1 else "こんにちは"

payload = json.dumps({
    "model": MODEL,
    "prompt": prompt,
    "stream": False,
}).encode("utf-8")

req = urllib.request.Request(
    OLLAMA_URL,
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)

with urllib.request.urlopen(req, timeout=120) as res:
    data = json.loads(res.read().decode("utf-8"))

print(data["response"])
