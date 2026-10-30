# kx3-sdr-sync

Use an SDRplay receiver running **SDRconnect** as a live panadapter for an **Elecraft KX3**. Tune the KX3 and the waterfall follows; click a signal in SDRconnect and the KX3 tunes to it.

```
KX3 --CAT--> rigctld :4534 <--> kx3_panadapter_sync.py <--> SDRconnect WebSocket API :5454
```

```
2026-10-29 12:04:51,210 INFO Connected to KX3 rigctld at 127.0.0.1:4534
2026-10-29 12:04:51,214 INFO KX3 -> SDRConnect: 14074000 Hz
2026-10-29 12:05:13,602 INFO SDRConnect -> KX3: 14076150 Hz     <- a click on the waterfall
2026-10-29 12:05:40,018 INFO KX3 -> SDRConnect: 7074000 Hz      <- band change on the radio
```

Both directions share one "last agreed frequency", so a change one side just made, echoing back from the other, is never taken for a new one. No ping-pong.

## Two versions

| | |
|---|---|
| `kx3_panadapter_sync.py` | both ways: the KX3 drives SDRconnect, and clicks in SDRconnect tune the KX3 |
| `kx3_panadapter_only_sync.py` | one way: SDRconnect follows the KX3, and the radio is never tuned |

## What you need

- **SDRconnect** with its WebSocket API turned on (the WebSocket setting in the app, or the headless server). Port 5454 by default.
- **rigctld** running for the KX3 (Hamlib model 2045). Port 4534 here, so it can sit next to another radio's rigctld on 4532.
- **[Rigctl Server for SDRConnect](https://github.com/W9KSB/Rigctl-Server-for-SDRConnect)** by W9KSB. These scripts use its SDRconnect client (`sdrconnect_netcontrol.py`). That code isn't copied here, so install his project and follow its requirements. Tested against its commit `994036b`.

Nothing here is KX3-specific except the default port. Any radio that `rigctld` can read and set the frequency of will work.

## Install

```
git clone https://github.com/W9KSB/Rigctl-Server-for-SDRConnect.git ~/Rigctl-Server-for-SDRConnect
git clone https://github.com/Saltyhaus/kx3-sdr-sync.git ~/kx3-sdr-sync
cd ~/kx3-sdr-sync
python3 kx3_panadapter_sync.py --verbose
```

The scripts find W9KSB's module in their own folder, in `~/Rigctl-Server-for-SDRConnect`, or wherever `SDRCONNECT_DIR` points.

Options: `--kx3-host`, `--kx3-port` (4534), `--sdr-port` (5454), `--device primary|secondary`, `--frequency-property device_vfo_frequency|device_center_frequency`, `--poll-interval` (0.25 s).

`device_vfo_frequency` moves the tuned frequency inside the span, while `device_center_frequency` moves the whole span. Pick whichever looks right on your waterfall.

### As a service

Edit `User` and the paths in `kx3-sdrconnect-sync.service`, then:

```
sudo cp kx3-sdrconnect-sync.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kx3-sdrconnect-sync
journalctl -u kx3-sdrconnect-sync -f
```

It reconnects by itself if rigctld or SDRconnect restarts.

## Tests

```
python3 -m unittest tests.test_sync
```

They run against a fake rigctld and a stand-in for the SDRconnect client, so no radio or SDR is needed.

## Thanks

To W9KSB, whose Rigctl Server for SDRConnect does the hard part of talking to SDRconnect. This is a small add-on to it.

## License

GPL-3.0-or-later, for the files in this repo. W9KSB's project is his own and is not included. 73 de AG7JC.
