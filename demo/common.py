import sys
import threading
import time

import uvicorn

PLATFORM = "http://127.0.0.1:8000"
AGENT_HOST = "http://127.0.0.1:9001"


def serve(app, port):
    """Run a FastAPI app in a background thread and wait until it accepts connections."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return
        time.sleep(0.05)
    sys.exit(f"server on :{port} failed to start (is the port already in use?)")
