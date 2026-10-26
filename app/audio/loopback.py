def select_loopback(pa):
    """Resolve the default WASAPI output's loopback; do not capture yet."""
    device = pa.get_default_wasapi_loopback()
    if not device.get("isLoopbackDevice") or device["maxInputChannels"] < 1:
        raise RuntimeError("WASAPI loopback device unavailable")
    return device
