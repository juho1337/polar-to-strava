from pathlib import Path

import pytest

from config.loader import load_config
from core.errors import ConfigurationError


def test_load_config_resolves_relative_paths(tmp_path: Path) -> None:
    export = tmp_path / "export"
    export.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text("polar_export: export\noutput_folder: output\nworkers: 2\n", encoding="utf-8")
    settings = load_config(config)
    assert settings.polar_export == export
    assert settings.output_folder == tmp_path / "output"
    assert settings.workers == 2


def test_load_config_rejects_unknown_keys(tmp_path: Path) -> None:
    export = tmp_path / "export"
    export.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text("polar_export: export\nunknown: value\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(config)
