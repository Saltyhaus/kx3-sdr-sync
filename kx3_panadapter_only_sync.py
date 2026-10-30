#!/usr/bin/env python3
"""Directly sync a KX3 (via its own rigctld) into SDRconnect's WebSocket API.

Panadapter-style: KX3 frequency changes push straight into SDRconnect.
No rigctl-server middle hop — this talks to the KX3's rigctld as a client
and to SDRconnect's WebSocket API as a client, in one process.

Usage:
  python3 kx3_panadapter_only_sync.py

Defaults:
  - polls KX3 rigctld at 127.0.0.1:4534
  - pushes into SDRconnect WebSocket at 127.0.0.1:5454
"""
from __future__ import annotations
import argparse
import asyncio
import contextlib
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
    """Minimal Hamlib rigctld network client: polls 'f' (frequency)."""

    def __init__(self, host: str, port: int, poll_interval: float) -> None:
        self.host = host
        self.port = port
        self.poll_interval = poll_interval
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
        self._reader = None
        self._writer = None

    async def _command(self, cmd: str) -> str:
        async with self._lock:
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

    async def poll(self, on_freq_change) -> None:
        last_freq: int | None = None
        while True:
            try:
                if self._writer is None:
                    await self.connect()
                    logging.info("Connected to KX3 rigctld at %s:%s", self.host, self.port)
                freq = await self.get_freq()
                if freq != last_freq:
                    last_freq = freq
                    await on_freq_change(freq)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.warning("KX3 rigctld connection issue: %s", exc)
                await self.close()
                await asyncio.sleep(2)
                continue
            await asyncio.sleep(self.poll_interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync KX3 frequency into SDRconnect (panadapter mode).")
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

    state = BridgeState()
    sdr = SDRConnectClient(
        port=args.sdr_port,
        device=args.device,
        frequency_property=args.frequency_property,
        state=state,
        request_timeout=1.5,
        refresh_interval=0,  # one-way sync — we never need to read freq back from SDRconnect
    )
    kx3 = KX3RigctlClient(args.kx3_host, args.kx3_port, args.poll_interval)

    async def on_freq_change(freq: int) -> None:
        if not sdr.state.connected:
            logging.debug("SDRConnect not connected yet, dropping KX3 freq %s", freq)
            return
        logging.info("KX3 -> SDRConnect: %s Hz", freq)
        await sdr.send_property(sdr.frequency_property, freq)

    sdr_task = asyncio.create_task(sdr.run())
    kx3_task = asyncio.create_task(kx3.poll(on_freq_change))

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
