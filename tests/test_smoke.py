import ast
import json
from pathlib import Path
import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]

def test_manifest_matches_the_delivered_contract():
    manifest = yaml.safe_load((ROOT / "agent.yaml").read_text())
    jsonschema.Draft202012Validator(json.loads((ROOT / "manifest.schema.json").read_text())).validate(manifest)

def test_entry_is_valid_python_and_has_an_async_entry():
    tree = ast.parse((ROOT / "app.py").read_text())
    assert any(isinstance(node, ast.AsyncFunctionDef) and node.name == "run" for node in tree.body)
