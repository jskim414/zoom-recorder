from datetime import datetime
from pathlib import Path

import pytest

from app.storage.session_store import (SESSION_NAME, SessionStore, available_session_path,
                                      find_recovery, suggest_folder_name, validate_folder_name)


@pytest.mark.parametrize("name", ["", "../outside", "20261026_01_../outside", "20261026_01_C:foo",
                                 "20261026_01_a\\b", "20261026_01_a\n", "20261026_01_a.",
                                 "20261026_01_a ", "20260230_01_", "20261026_00_", "CON",
                                 "20261026_01_" + "a" * 120])
def test_invalid_names_never_create_files(tmp_path, name):
    with pytest.raises(ValueError):
        SessionStore.create(tmp_path, "manual", "consented", name)
    assert not list(tmp_path.iterdir())


def test_date_sequence_unicode_and_collision(tmp_path):
    assert suggest_folder_name(tmp_path, now=datetime(2026, 10, 26)) == "20261026_01_"
    store = SessionStore.create(tmp_path, "manual", "consented", "20261026_01_주간 회의")
    (tmp_path / "20261026_99_다른 회의").mkdir()
    assert suggest_folder_name(tmp_path, now=datetime(2026, 10, 26)) == "20261026_100_"
    assert suggest_folder_name(tmp_path, now=datetime(2026, 10, 27)) == "20261027_01_"
    before = (store.directory / "metadata.json").read_bytes()
    with pytest.raises(FileExistsError):
        SessionStore.create(tmp_path, "manual", "consented", store.directory.name)
    assert (store.directory / "metadata.json").read_bytes() == before
    (tmp_path / "20261026_02_Meeting").mkdir()
    with pytest.raises(FileExistsError):
        available_session_path(tmp_path, "20261026_02_meeting")


def test_rename_preserves_uuid_audio_and_recovery(tmp_path, monkeypatch):
    store = SessionStore.create(tmp_path, "manual", "consented", "20261026_01_")
    original = store.directory
    session_id = store.metadata["session_id"]
    audio = original / "audio/remote_0001.wav"
    audio.write_bytes(b"test fixture; not recorded audio")
    with pytest.raises(RuntimeError):
        store.rename("20261026_01_회의")
    store.update(state="FINALIZING")
    occupied = tmp_path / "20261026_02_"
    occupied.mkdir()
    with pytest.raises(FileExistsError):
        store.rename(occupied.name)
    def fail_rename(*args):
        raise PermissionError("test: open file handle")
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "rename", fail_rename)
        with pytest.raises(PermissionError):
            store.rename("20261026_01_회의")
    assert store.directory == original and audio.exists()
    store.rename("20261026_01_회의")
    assert not original.exists()
    assert (store.directory / "audio/remote_0001.wav").read_bytes() == b"test fixture; not recorded audio"
    assert SessionStore(store.directory).metadata["session_id"] == session_id
    assert set(find_recovery(tmp_path)) == {store.directory, occupied}


def test_legacy_and_new_names_are_discoverable(tmp_path):
    old_name = "2026-10-23_140000_" + "a" * 32
    new_name = validate_folder_name("20261026_01_회의")
    store = SessionStore.create(tmp_path, "manual", "consented", new_name)
    legacy = tmp_path / old_name
    store.directory.rename(legacy)
    assert SESSION_NAME.fullmatch(old_name) and SESSION_NAME.fullmatch(new_name)
    assert find_recovery(tmp_path) == [legacy]
