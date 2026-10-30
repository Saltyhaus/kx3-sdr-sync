"""Test stand-in for W9KSB's sdrconnect_netcontrol: just the parts the sync
scripts use. The real module is not part of this repo."""


class BridgeState:
    def __init__(self):
        self.connected = True


class SDRConnectClient:
    def __init__(self, port=5454, device="primary", frequency_property="device_vfo_frequency",
                 state=None, request_timeout=1.5, refresh_interval=0, **_):
        self.frequency_property = frequency_property
        self.state = state or BridgeState()
        self.sent = []

    def _handle_message(self, raw):
        pass

    async def send_property(self, prop, value):
        self.sent.append((prop, value))
