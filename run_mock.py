import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

import uvicorn

if __name__ == "__main__":
    uvicorn.run("mock_api.main:app", host="127.0.0.1", port=8001, reload=True)
