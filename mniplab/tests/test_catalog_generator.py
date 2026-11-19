"""The catalogue generator preserves an Instructor's Area and teaching order."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from util import gen_labs_json

pytestmark = pytest.mark.unit


def test_refresh_keeps_areas_and_catalogue_order(tmp_path: Path) -> None:
    """Refreshing metadata keeps the chosen order, even when ids sort differently."""
    path = tmp_path / "labs.json"
    rows = [{"id": "z-lesson", "area": "Static"},
            {"id": "a-lesson", "area": "Inter-domain"}]
    path.write_text(json.dumps(rows), encoding="utf-8")
    with patch.object(gen_labs_json, "LABS_JSON_PATH", str(path)), \
         patch.object(gen_labs_json, "find_lab_scripts", return_value=[]):
        assert gen_labs_json.mode_all() == 0
    assert json.loads(path.read_text(encoding="utf-8")) == rows
