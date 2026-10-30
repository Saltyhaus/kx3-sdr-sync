#!/usr/bin/env python3
"""Bidirectional CAT sync between a KX3 (via rigctld) and SDRconnect.

- KX3 -> SDRconnect: polls the KX3's rigctld and pushes frequency changes
  into SDRconnect's WebSocket API (panadapter following).
- SDRconnect -> KX3: when SDRconnect reports a frequency change we didn't
  just cause ourselves (e.g. a click-to-tune in the SDRconnect UI), it's
  sent to the KX3's rigctld as a set_freq command.

A shared "last synced frequency" is checked before either side acts, so
an echo of a change we just made is never mistaken for a new change from
the other side (no ping-pong).
"""
from __future__ import annotations
import argparse
import asyncio
import contextlib
import json
import logging
import signal

import os
import sys
from pathlib import Path

# W9KSB's Rigctl Server for SDRConnect provides the SDRconnect client. It
# isn't copied here: install it, then drop these scripts into its folder,
# or point SDRCONNECT_DIR at it.
for _d in (os.environ.get("SDRCONNECT_DIR"), str(Path(__file__).resolve().parent),
           str(Path.home() / "Rigctl-Server-for-SDRConnect")):
    if _d and Path(_d, "sdrconnect_netcontrol.py").is_file() and _d not in sys.path:
        sys.path.insert(0, _d)
        break
try:
    from sdrconnect_netcontrol import SDRConnectClient, BridgeState
except ImportError:
    sys.exit("Can't find sdrconnect_netcontrol.py from W9KSB's Rigctl Server for SDRConnect\n"
             "(https://github.com/W9KSB/Rigctl-Server-for-SDRConnect). Put this script in its\n"
             "folder, or set SDRCONNECT_DIR=/path/to/Rigctl-Server-for-SDRConnect.")


class KX3RigctlClient:
    """Minimal Hamlib rigctld network client: get/set frequency ('f'/'F')."""

    def __init__(self, host: str, port: int, poll_interval: float) -> None:
        self.host = host
        self.port = port
        self.poll_interval = poll_interval
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._io_lock = asyncio.Lock()

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
        self._reader = None
        self._writer = None

    async def ensure_connected(self) -> None:
        if self._writer is None:
            await self.connect()
            logging.info("Connected to KX3 rigctld at %s:%s", self.host, self.port)

    async def _command(self, cmd: str) -> str:
        async with self._io_lock:
            if self._writer is None or self._reader is None:
                raise ConnectionError("not connected to rigctld")
            self._writer.write((cmd + "\n").encode())
            await self._writer.drain()
            line = await asyncio.wait_for(self._reader.readline(), timeout=2.0)
            if not line:
                raise ConnectionError("rigctld closed connection")
            return line.decode(errors="replace").strip()

    async def get_freq(self) -> int:
        return int(await self._command("f"))

    async def set_freq(self, freq: int) -> None:
        response = await self._command(f"F {freq}")
        if not response.startswith("RPRT 0"):
            raise ConnectionError(f"KX3 rigctld rejected set_freq: {response!r}")


class SyncState:
    """Tracks the last frequency both sides are known to agree on, so an
    echo of our own change is never mistaken for a change from the other
    side."""

    def __init__(self) -> None:
        self.last_synced_freq: int | None = None
        self._lock = asyncio.Lock()

    async def claim_if_new(self, freq: int) -> bool:
        async with self._lock:
            if freq == self.last_synced_freq:
                return False
            self.last_synced_freq = freq
            return True


class BidirectionalSDRConnectClient(SDRConnectClient):
    def __init__(self, *args, sync_state: SyncState, kx3: KX3RigctlClient, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sync_state = sync_state
        self.kx3 = kx3

    def _handle_message(self, raw: str) -> None:
        super()._handle_message(raw)
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return
        if message.get("event_type") != "property_changed":
            return
        if message.get("property") != self.frequency_property:
            return
        with contextlib.suppress(ValueError):
            freq = int(float(message.get("value", "")))
            asyncio.ensure_future(self._maybe_push_to_kx3(freq))

    async def _maybe_push_to_kx3(self, freq: int) -> None:
        if not await self.sync_state.claim_if_new(freq):
            return  # echo of a change we already know about
        logging.info("SDRConnect -> KX3: %s Hz", freq)
        try:
            await self.kx3.ensure_connected()
            await self.kx3.set_freq(freq)
        except Exception as exc:
            logging.warning("Failed to push frequency to KX3: %s", exc)


async def poll_kx3(kx3: KX3RigctlClient, sdr: SDRConnectClient, sync_state: SyncState) -> None:
    while True:
        try:
            await kx3.ensure_connected()
            freq = await kx3.get_freq()
            if await sync_state.claim_if_new(freq):
                if sdr.state.connected:
                    logging.info("KX3 -> SDRConnect: %s Hz", freq)
                    await sdr.send_property(sdr.frequency_property, freq)
                else:
                    logging.debug("SDRConnect not connected yet, dropping KX3 freq %s", freq)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.warning("KX3 rigctld connection issue: %s", exc)
            await kx3.close()
            await asyncio.sleep(2)
            continue
        await asyncio.sleep(kx3.poll_interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bidirectional KX3 <-> SDRconnect frequency sync.")
    parser.add_argument("--kx3-host", default="127.0.0.1")
    parser.add_argument("--kx3-port", type=int, default=4534)
    parser.add_argument("--sdr-port", type=int, default=5454, help="local SDRConnect WebSocket port")
    parser.add_argument("--device", default="primary", choices=["primary", "secondary"])
    parser.add_argument(
        "--frequency-property",
        default="device_vfo_frequency",
        choices=["device_vfo_frequency", "device_center_frequency"],
    )
    parser.add_argument("--poll-interval", type=float, default=0.25, help="seconds between KX3 frequency polls")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    sync_state = SyncState()
    kx3 = KX3RigctlClient(args.kx3_host, args.kx3_port, args.poll_interval)

    state = BridgeState()
    sdr = BidirectionalSDRConnectClient(
        port=args.sdr_port,
        device=args.device,
        frequency_property=args.frequency_property,
        state=state,
        request_timeout=1.5,
        refresh_interval=0,
        sync_state=sync_state,
        kx3=kx3,
    )

    sdr_task = asyncio.create_task(sdr.run())
    kx3_task = asyncio.create_task(poll_kx3(kx3, sdr, sync_state))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signame):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(getattr(signal, signame), stop.set)

    await stop.wait()
    sdr.stop()
    kx3_task.cancel()
    await asyncio.gather(sdr_task, kx3_task, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
