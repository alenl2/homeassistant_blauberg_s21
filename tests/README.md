# Tests

```bash
pip install -r requirements_test.txt
pytest
```

Everything runs offline by default. No IP addresses or credentials are stored in
this repository — the hardware tests read the address from the environment.

## Layout

| File | What it covers | Needs hardware |
| --- | --- | --- |
| `test_metadata.py` | `manifest.json`, `services.yaml`, `strings.json` and every `translations/*.json` agree with each other and with the code | no |
| `test_client.py` | the vendored Modbus client: register decoding, retries, validation and the socket lifecycle | no |
| `test_integration.py` | setup/unload, the coordinator, and all four entity platforms | no |
| `test_live.py` | the same behaviour against a real unit | yes |

Supporting modules:

- **`fake_s21.py`** — an asyncio Modbus TCP server that imitates a real S21,
  including the behaviour that drives the whole design of the client: it accepts
  **exactly one** connection and resets every other attempt. It binds to the
  loopback interface on an ephemeral port, and can inject faults (refused
  connections, hangs, exception responses, mid-request disconnects, a wrong
  device type).
- **`hass_stub.py`** — a small stand-in for the Home Assistant APIs the
  integration uses. See its docstring for why it exists, what it is faithful
  about, and what it deliberately does not do. It steps aside automatically if a
  real Home Assistant is importable, in which case `test_integration.py` skips.

## Running against real hardware

The hardware tests are skipped unless `BLAUBERG_S21_HOST` names a unit.

```bash
# bash
BLAUBERG_S21_HOST=192.0.2.10 pytest tests/test_live.py -v
```

```powershell
# PowerShell
$env:BLAUBERG_S21_HOST = "192.0.2.10"; pytest tests/test_live.py -v
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `BLAUBERG_S21_HOST` | *unset* | address of the unit; unset means skip |
| `BLAUBERG_S21_PORT` | `502` | Modbus TCP port |
| `BLAUBERG_S21_ALLOW_WRITES` | *unset* | set to `1` to also exercise the write paths |
| `BLAUBERG_S21_SOAK_SECONDS` | `60` | duration of the soak test |

The hardware tests are read-only unless `BLAUBERG_S21_ALLOW_WRITES=1`, and even
then every write puts back the value that was just read, so the unit's
configuration is never changed.

Two commands are **never** exercised against real hardware, because they cannot
be undone or would interrupt ventilation:

- `reset_filter_change_timer`, which would wipe the real filter countdown
- powering the unit off

Both are covered against the fake server instead.

## Why the socket tests matter

The unit accepts a single Modbus TCP connection and keeps that slot reserved for
as long as a peer holds the socket open. Two bugs used to leave a socket behind,
which locked the integration out of its own device until Home Assistant was
restarted again:

- Home Assistant does not unload config entries on shutdown, so the socket was
  never closed on a restart.
- `connect()` was wrapped in `asyncio.wait_for`, and cancelling the handshake
  orphaned a socket that `close()` could no longer reclaim.

`test_client.py` and `test_integration.py` pin both down against the fake server,
so the regressions stay covered without needing the hardware.
