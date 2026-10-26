import pytest

from app.settings import Settings, load_settings


@pytest.mark.parametrize("values", [dict(segment_seconds=0), dict(segment_seconds=301), dict(segment_seconds="120"),
                                     dict(recordings_dir=""), dict(microphone_index=-1), dict(retention_days=0),
                                     dict(auto_transcribe="false"), dict(auto_detect="false"), dict(extension_ids=[None]),
                                     dict(extension_ids=["chrome-extension://bad"]), dict(bridge_port=80),
                                     dict(stable_seconds=0), dict(meeting_title_patterns="zoom")])
def test_bad_settings_fail_closed(values):
    with pytest.raises(ValueError):
        Settings(**values).validate()


def test_example_config_and_unknown_keys(tmp_path):
    settings = load_settings("config/settings.example.yaml")
    assert settings.retention_days is None and not settings.extension_ids
    path = tmp_path / "settings.yaml"
    path.write_text("auto_record: true", encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(path)
