def default_endpoint_ids():
    """Read device identity only, without opening a recording stream."""
    from pycaw.pycaw import AudioUtilities
    enumerator = AudioUtilities.GetDeviceEnumerator()
    # eRender/eCapture with eConsole matches PortAudio WASAPI's default role.
    return tuple(enumerator.GetDefaultAudioEndpoint(flow, 0).GetId() for flow in (0, 1))
