from pathlib import Path

from app import paths
from app.settings import Settings


def test_workspace_paths_independent_of_cwd(tmp_path, monkeypatch):
    source = tmp_path / "application"
    source.mkdir()
    (tmp_path / ".zoom-workspace").touch()
    monkeypatch.setattr(paths, "source_root", lambda: source)
    monkeypatch.chdir(tmp_path.parent)
    options = paths.resolve_settings_paths(Settings())
    assert paths.data_root() == tmp_path / "data"
    assert paths.logs_root() == tmp_path / "logs"
    assert paths.distribution_root() == tmp_path / "releases"
    assert Path(options.recordings_dir) == tmp_path / "data/recordings"
    assert Path(options.model) == tmp_path / "data/models/small"


def test_independent_clone_needs_no_parent_documents(tmp_path, monkeypatch):
    source = tmp_path / "clone"
    source.mkdir()
    monkeypatch.setattr(paths, "source_root", lambda: source)
    assert paths.default_config() == source / "data/config/settings.yaml"
    assert paths.artifacts_root() == source / "logs/validation-artifacts"
    assert paths.distribution_root() == source / "dist"
    custom = str(tmp_path / "custom-model")
    assert paths.resolve_settings_paths(Settings(model=custom)).model == custom
