import tomllib
from pathlib import Path

import yaml

from app.core.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_package_runtime_and_openapi_versions_match() -> None:
    package = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    specification = yaml.safe_load((PROJECT_ROOT / "docs" / "openapi.yaml").read_text())

    assert package["project"]["version"] == "0.2.0"
    assert Settings(_env_file=None).app_version == package["project"]["version"]
    assert specification["info"]["version"] == package["project"]["version"]
