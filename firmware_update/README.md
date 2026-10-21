# HRU400 firmware update (local, no vendor account/backend)

Reimplementation of the ITHO Daalderop Service App's local firmware-update
protocol, built from static analysis of the app's own JS bundle (current
version, chunk hashes `main.433bf2812623f36cacaa.js` /
`13.e3c7bfe841f06511cd7d.js`). Talks only to the device's own local Wi-Fi AP
at `192.168.4.1` — no cloud backend, no account, no API key involved.

Product: **WTW-unit HRU 400** (article `03-00632`).

## Files

| File | What it is |
|---|---|
| `flash_base_appliance.py` | Flashes the BASE appliance firmware (the main unit, i2c address `82`) |
| `flash_wifi_module.py` | Flashes the Wi-Fi/RF module firmware (address `8A`) |
| `hru400_BASE_8.1.bin` | BASE firmware v8.1 (id 32). SHA-256: `9e36aafc6e62529a1251d50407ed8c4532739c760eb67b3ae9855af20de6d108` |
| `hru400_WIFI_21.2.bin` | Wi-Fi module firmware v21.2 (id 31). SHA-256: `ab7cdbc4ca51f52aea8acdae4cae7b0e36d40daec8ba6837c25e42528c636586` |
| `firmware_metadata.json` | The server's own firmware metadata for this product (captured from a real authenticated session) — `hash_text`, `has_eeprom`, `apply_factory_reset_after_update`, etc. for every firmware entry seen |

The required `--image-hash` for the BASE firmware is **`b17a4f803f4b60bd98bdd827258883b89e389b208539e02947019c9ae662c525`**
(`firmware_metadata.json` → BASE id 32 → `hash_text`). This is the hash of
the *decrypted* image, not of the `.bin` you upload — the source file is
`.hex.enc` — so it will not match a SHA-256 of the file itself; that's
expected, not a bug. The Wi-Fi module firmware needs no hash (`hash_text` is
empty for every WIFI entry).

## Before you touch real hardware

1. Join your laptop/phone to the device's own Wi-Fi access point (not your
   home network) — the device must be reachable at `192.168.4.1`.
2. Run with `--dry-run` first on both scripts and read the printed plan.
3. Don't power-cycle the device mid-update.

## Commands

Dry run (prints the full plan, touches nothing):
```sh
python3 flash_base_appliance.py hru400_BASE_8.1.bin \
  --image-hash b17a4f803f4b60bd98bdd827258883b89e389b208539e02947019c9ae662c525 \
  --dry-run

python3 flash_wifi_module.py hru400_WIFI_21.2.bin --dry-run
```

Real run:
```sh
python3 flash_base_appliance.py hru400_BASE_8.1.bin \
  --image-hash b17a4f803f4b60bd98bdd827258883b89e389b208539e02947019c9ae662c525

python3 flash_wifi_module.py hru400_WIFI_21.2.bin
```

## Important: automatic factory reset

For **this specific BASE firmware** (`apply_factory_reset_after_update: true`
in `firmware_metadata.json`), the real app automatically sends the device a
**FactoryReset** command right after a successful update — wiping the
device's current settings back to factory defaults. `flash_base_appliance.py`
replicates this by default, same as the app. To skip it:

```sh
python3 flash_base_appliance.py hru400_BASE_8.1.bin \
  --image-hash b17a4f803f4b60bd98bdd827258883b89e389b208539e02947019c9ae662c525 \
  --skip-factory-reset
```

The Wi-Fi module firmware does **not** trigger a factory reset
(`apply_factory_reset_after_update: false` for every WIFI entry) —
`flash_wifi_module.py` has no such step.

## What's confirmed vs. translated

Confirmed line-for-line against the app's current source: block chunking
(8192-byte blocks), endpoints and headers, no retry on a single block
failure, the single-vs-double `start-update`/`apply-update` calls and their
timing, the EEPROM chaining (not needed for this firmware —
`has_eeprom: false`), and the FactoryReset I2C message
(`82 8A CA BC 06 01 01 66`, POSTed to `/i2c-bus/message`).

One thing is a deliberate translation rather than a literal match: the app's
whole-phase-restart check is keyed off Angular's `statusText === "Unknown
error"` / `isTrusted`, a browser-XHR-only signal with no Python equivalent.
Both scripts treat "no real HTTP response came back at all" (connection
refused, timeout, no route) as the equivalent case, and do *not* restart on
a real HTTP error status from the device — matching the source's intent as
closely as a different HTTP stack allows.

## Scope

This only replicates the **local device protocol** (phone/laptop ↔ device
over its own AP). It does not call, nor need, ITHO's cloud backend or any
account/API key — the firmware files and their metadata here were already
captured from a legitimate session and are reused as static inputs.
