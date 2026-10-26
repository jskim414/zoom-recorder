def select_microphone(pa, api_type, index=None):
    api = pa.get_host_api_info_by_type(api_type)
    device = pa.get_device_info_by_index(api["defaultInputDevice"] if index is None else index)
    if device["hostApi"] != api["index"] or device.get("isLoopbackDevice") or device["maxInputChannels"] < 1:
        raise RuntimeError("Select a WASAPI microphone input")
    return device
