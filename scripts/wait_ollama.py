"""Wait for the in-container model service without any external requests."""

import time
import urllib.error
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
deadline = time.monotonic() + 120
while True:
    try:
        with opener.open("http://127.0.0.1:11434/api/tags", timeout=2) as response:
            if response.status == 200:
                break
    except (urllib.error.URLError, OSError):
        pass
    if time.monotonic() >= deadline:
        raise SystemExit("Ollama did not become ready; inspect /result/logs/ollama.log")
    time.sleep(0.5)
