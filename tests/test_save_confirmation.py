import pytest

from app.audio.recorder import Recorder
from app.controller import Controller
from app.detection.session_manager import State
from app.settings import Settings
from test_recorder_controller import FakeBackend, backend_factory, spin_until


@pytest.mark.parametrize("rename", [True, False])
def test_closed_audio_waits_for_name_before_transcription(tmp_path, rename):
    backend = FakeBackend()
    calls = []
    class Transcriber:
        def __init__(self, settings):
            pass
        def run(self, store):
            calls.append(store.directory)
            assert len(list((store.directory / "audio").glob("*.wav"))) == 2
            assert not list((store.directory / "audio").glob("*.part"))
            store.update(state="COMPLETE")
    controller = Controller(Settings(recordings_dir=str(tmp_path), auto_transcribe=True),
                            lambda store, options: Recorder(store, options, backend_factory(backend)), Transcriber)
    controller.require_save_confirmation = True
    controller.manager.manual()
    key = next(iter(controller.manager.candidates))
    controller.start(key, True, True, folder_name="20261026_01_")
    assert controller.recorder.ready.wait(2)
    recorder = controller.recorder
    with pytest.raises(RuntimeError):
        controller.confirm_save("20261026_01_회의")
    controller.stop()
    assert recorder.stop_requested.is_set()
    spin_until(lambda: (controller.poll() or controller.save_pending))
    assert recorder.done.is_set() and controller.recorder is None
    assert controller.busy and controller.manager.state == State.FINALIZING and not calls
    original = controller.store.directory
    wav_bytes = {p.name: p.read_bytes() for p in (original / "audio").glob("*.wav")}
    (tmp_path / "20261026_02_").mkdir()
    with pytest.raises(FileExistsError):
        controller.confirm_save("20261026_02_")
    assert controller.save_pending and controller.store.directory == original and not calls
    controller.confirm_save("20261026_01_회의" if rename else None)
    spin_until(lambda: (controller.poll() or controller.manager.state == State.COMPLETE))
    expected = tmp_path / ("20261026_01_회의" if rename else "20261026_01_")
    assert calls == [expected] and not controller.save_pending
    assert {p.name: p.read_bytes() for p in (expected / "audio").glob("*.wav")} == wav_bytes
    assert controller.manager.history[-3:] == [State.FINALIZING, State.TRANSCRIBING, State.COMPLETE]
    controller.close()


def test_invalid_start_name_and_missing_consent_never_capture(tmp_path):
    calls = []
    controller = Controller(Settings(recordings_dir=str(tmp_path)), lambda *args: calls.append(args))
    controller.manager.manual()
    key = next(iter(controller.manager.candidates))
    with pytest.raises(ValueError):
        controller.start(key, True, True, folder_name="../outside")
    with pytest.raises(PermissionError):
        controller.start(key, False, True, folder_name="20261026_01_")
    assert controller.manager.state == State.PROMPTED and not calls and not list(tmp_path.iterdir())
    controller.close()
