"""Named local portfolio chart assets stay behind the frozen review boundary."""

from __future__ import annotations

import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from alphalab.research.workbench import create_workbench_server


class _ReviewFlow:
    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir


@pytest.fixture()
def workbench(tmp_path: Path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    server = create_workbench_server(_ReviewFlow(runs_dir))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _url(server, path: str) -> str:
    return f"http://{server.server_address[0]}:{server.server_address[1]}{path}"


def test_named_chart_assets_are_served_without_loading_a_run(workbench):
    for asset in ("portfolio-review-charts.js", "vendor/lightweight-charts.standalone.production.js"):
        with urlopen(_url(workbench, f"/research/review/missing-run/{asset}")) as response:
            body = response.read()
        assert response.status == 200
        assert body


def test_review_static_asset_path_traversal_is_rejected_before_run_loading(workbench):
    for path in ("/research/review/missing-run/../app.js", "/research/review/missing-run/api/../app.js"):
        with pytest.raises(HTTPError) as error:
            urlopen(_url(workbench, path))
        assert error.value.code == 404
