import asyncio
import json
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ["SDRCONNECT_DIR"] = str(HERE / "stub")
sys.path.insert(0, str(HERE.parent))
import kx3_panadapter_sync as sync          # noqa: E402


class FakeRigctld:
    """rigctld over TCP: 'f' -> frequency, 'F n' -> set it."""
    def __init__(self, freq=14074000):
        self.freq, self.sets = freq, []

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def handle(self, r, w):
        while line := await r.readline():
            cmd = line.decode().strip()
            if cmd == "f":
                w.write(f"{self.freq}\n".encode())
            elif cmd.startswith("F "):
                self.freq = int(cmd[2:]); self.sets.append(self.freq)
                w.write(b"RPRT 0\n")
            await w.drain()


class Sync(unittest.IsolatedAsyncioTestCase):
    async def test_echo_is_not_a_new_change(self):
        s = sync.SyncState()
        self.assertTrue(await s.claim_if_new(7074000))
        self.assertFalse(await s.claim_if_new(7074000))
        self.assertTrue(await s.claim_if_new(14074000))

    async def test_rigctl_get_set(self):
        rig = FakeRigctld(); port = await rig.start()
        kx3 = sync.KX3RigctlClient("127.0.0.1", port, 0.05)
        await kx3.ensure_connected()
        self.assertEqual(await kx3.get_freq(), 14074000)
        await kx3.set_freq(7100000)
        self.assertEqual(rig.sets, [7100000])
        await kx3.close(); rig.server.close()

    async def test_click_to_tune_reaches_kx3_but_not_our_own_echo(self):
        rig = FakeRigctld(); port = await rig.start()
        state = sync.SyncState()
        kx3 = sync.KX3RigctlClient("127.0.0.1", port, 0.05)
        sdr = sync.BidirectionalSDRConnectClient(sync_state=state, kx3=kx3)
        msg = lambda f, prop="device_vfo_frequency": json.dumps(
            {"event_type": "property_changed", "property": prop, "value": str(f)})
        sdr._handle_message(msg(7100000))                     # a click in SDRconnect
        sdr._handle_message(msg(7200000, prop="something_else"))
        await asyncio.sleep(0.2)
        self.assertEqual(rig.sets, [7100000])
        await state.claim_if_new(14074000)                    # we just pushed this to SDRconnect...
        sdr._handle_message(msg(14074000))                    # ...and it echoes back
        await asyncio.sleep(0.2)
        self.assertEqual(rig.sets, [7100000])
        await kx3.close(); rig.server.close()

    async def test_kx3_change_follows_into_sdrconnect(self):
        rig = FakeRigctld(); port = await rig.start()
        state = sync.SyncState()
        kx3 = sync.KX3RigctlClient("127.0.0.1", port, 0.05)
        sdr = sync.BidirectionalSDRConnectClient(sync_state=state, kx3=kx3)
        task = asyncio.create_task(sync.poll_kx3(kx3, sdr, state))
        await asyncio.sleep(0.15)
        rig.freq = 3573000
        await asyncio.sleep(0.15)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(sdr.sent, [("device_vfo_frequency", 14074000), ("device_vfo_frequency", 3573000)])
        await kx3.close(); rig.server.close()


class Missing(unittest.TestCase):
    def test_clear_message_without_w9ksb_module(self):
        import subprocess
        env = dict(os.environ, SDRCONNECT_DIR="/nonexistent", HOME="/nonexistent")
        r = subprocess.run([sys.executable, str(HERE.parent / "kx3_panadapter_only_sync.py"), "--help"],
                           capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("W9KSB", r.stderr)


if __name__ == "__main__":
    unittest.main()
