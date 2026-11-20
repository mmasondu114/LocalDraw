from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from comic_workflow.engine import save
from comic_workflow.schemas import SCHEMAS

for name, schema in SCHEMAS.items():
    save(
        root / "schemas" / (name + ".schema.json"),
        {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema},
    )
