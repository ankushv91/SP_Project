import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://schema:schema@localhost/schema",
)

from app.main import app

openapi_path = Path(__file__).resolve().parents[1] / "openapi.json"
openapi_path.write_text(
    json.dumps(app.openapi(), indent=2) + "\n",
    encoding="utf-8",
)
print(f"Generated {openapi_path}")
