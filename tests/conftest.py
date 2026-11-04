from pathlib import Path

import pytest

from datamate.config import Config, load_config
from datamate.data.ingest import ingest_business
from datamate.types import Catalog

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = load_config(ROOT / "configs/default.yaml")
    cfg.output_root = tmp_path / "outputs"
    return cfg


@pytest.fixture(scope="session")
def catalog(tmp_path_factory: pytest.TempPathFactory) -> Catalog:
    cfg = load_config(ROOT / "configs/default.yaml")
    return ingest_business(cfg.data_root, "biz_demo", tmp_path_factory.mktemp("catalog"))
