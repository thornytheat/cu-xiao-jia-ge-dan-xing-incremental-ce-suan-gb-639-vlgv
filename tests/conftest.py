"""测试固定装置：临时库、固定样例目录、离线样例集。

约定：不读 datetime.now() 做期望值；所有时间断言用样例数据里的固定日期。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app import config                                              # noqa: E402
from app.main import create_app                                     # noqa: E402
from app.store import Store                                         # noqa: E402
from app.modules.m5_lift import service as lift_service             # noqa: E402
from app.modules.m1_ingest import reader                            # noqa: E402

SAMPLE_DIR = ROOT / "sample_data"


@pytest.fixture()
def settings(tmp_path) -> config.Settings:
    return config.Settings(db_path=str(tmp_path / "pricelift.sqlite"),
                           sample_dir=str(SAMPLE_DIR),
                           export_dir=str(tmp_path / "export"))


@pytest.fixture()
def store(settings) -> Store:
    return Store(settings.db_path)


@pytest.fixture()
def run(settings, store) -> dict:
    return lift_service.run(settings=settings, store=store, dataset="main")


@pytest.fixture()
def batch_id(run) -> str:
    return run["batch"]["batch_id"]


@pytest.fixture()
def app(settings):
    return create_app(settings)


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture()
def client_run(client) -> TestClient:
    response = client.post("/api/import", json={"dataset": "main"})
    assert response.status_code == 200
    assert response.json()["status"] == "done"
    return client


@pytest.fixture()
def sample_dir() -> pathlib.Path:
    return SAMPLE_DIR


@pytest.fixture()
def read_sales():
    def _read(name="daily_sales_gb18030.csv", role="sales"):
        return reader.read_file(SAMPLE_DIR / name, role)
    return _read
