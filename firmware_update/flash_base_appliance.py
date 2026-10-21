#!/usr/bin/env python3
"""
Standalone BASE-appliance firmware flasher for ITHO Daalderop ventilation units.

Replicates the local update protocol used by the official "ITHO Daalderop
Service App" for the i2c-connected base appliance (as opposed to the WiFi
module itself - see flash_wifi_module.py for that path). Source:
apk-pretty/*.js, IWifiModuleRestApi.sendFirmwareToAppliance /
applianceStartUpdate / getUpdateStatusAppliance, ApFirmwareUpdateService.
updateApplianceFirmware, and the orchestration in ApFirmwareUpdatePage.
step_5_updateFirmware (13.f1220dcfea4e7bc3f73b.js).

This talks ONLY to the device's own local access point (default 192.168.4.1),
which proxies these calls to the base appliance over its internal i2c bus.
No vendor backend / account is involved.

IMPORTANT: the "start-update" call needs an imageHash value. In the app this
is the hash_text the server returns alongside the firmware metadata when you
fetch/download it - NOT something computed from the .bin file locally. The
source uses the SAME hash_text for both the main image and the EEPROM image
(FirmwareFile's constructor sets `this.hash = e.hash_text` regardless of the
eeprom flag) - so --image-hash is reused for both phases below, matching that.

Two behaviours confirmed from step_5_updateFirmware that are easy to miss by
only reading the lower-level upload functions:

1. EEPROM chaining: if the firmware record has_eeprom, the app automatically
   runs a SECOND full pass against the /eeprom endpoints right after the main
   /firmware pass succeeds - not something you trigger separately. Pass
   --eeprom-file to replicate this; without it, only the main pass runs.

2. Whole-phase restart: each phase (main, then eeprom) gets its own retry
   counter, independent of the no-retry-per-block behaviour in send_blocks.
   On failure, the WHOLE phase (not just the failed block) is restarted from
   byte 0, up to 2 times (3 attempts total), but ONLY when:
     - the failure happened on the very first block of that phase
       (0 === wifiModule.block in the source), OR
     - the error looks like Angular's "Unknown error" / isTrusted signal,
       which in a browser means "no real HTTP response came back at all"
       (e.g. the request never reached a server) - NOT a real HTTP error
       status from the device.
   There is no literal Python equivalent of "Unknown error"/isTrusted (that's
   an XHR-specific signal); this script treats "no HTTP response at all"
   (connection refused, timeout, no route - urllib.error.URLError without an
   HTTP status, or a socket timeout) as the equivalent case, and does NOT
   retry on an HTTPError that carries a real status code from the device,
   matching the source's intent as closely as a different HTTP stack allows.
   This is a deliberate, disclosed translation, not a literal byte-for-byte
   match of something that only exists inside a browser.

3. Factory reset after success (ApFirmwareUpdatePage.after_update /
   reset_to_factory, current app version, 13.e3c7bfe841f06511cd7d.js): once
   ALL phases (main, then eeprom if present) finish successfully, if the
   firmware's apply_factory_reset_after_update metadata flag is true - which
   it IS for this HRU400 BASE v8.1 firmware (confirmed in out_body.json) -
   the app automatically sends the device a FactoryReset I2C command:
   POST /i2c-bus/message  {"message": "82 8A CA BC 06 01 01 66"}
   (a distinct constant from "ResetProduct", "...06 01 00 67", which this
   path does not use). Unlike block uploads, this specific call DOES retry
   (wifiModuleAutoRetry=3, msBetweenCalls=100ms), confirmed from
   sendCustomI2cMessage's own retry wrapper. THIS WIPES THE DEVICE'S CURRENT
   SETTINGS BACK TO FACTORY DEFAULTS. It fires by default here, matching the
   app's unconditional behaviour for this firmware - pass --skip-factory-reset
   to opt out.

Usage:
    python3 flash_base_appliance.py base.bin --image-hash <hash_text>
    python3 flash_base_appliance.py base.bin --image-hash <hash_text> \\
        --eeprom-file base_eeprom.bin
    python3 flash_base_appliance.py base.bin --image-hash <hash_text> --dry-run
    python3 flash_base_appliance.py base.bin --image-hash <hash_text> \\
        --skip-factory-reset
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

UPLOAD_SIZE = 8192        # blockSize used by the app
BLOCK_TIMEOUT = 60        # 6e4 ms on the block POST
START_UPDATE_TIMEOUT = 50  # 5e4 ms on the start-update POST
WAIT_BETWEEN_CALLS = 0.1  # pacing between blocks only, not a retry delay -
                           # a failed block is NOT retried within a phase
                           # (sendApplicanceFirmwareUpdateParts has no retry
                           # wrapper); see module docstring for the separate,
                           # whole-phase restart that DOES exist above it.
SECOND_START_UPDATE_DELAY = 8  # app waits 8s then fires start-update again
MAX_PHASE_RESTARTS = 2          # step_5_updateFirmware's "n < 2"

FACTORY_RESET_MESSAGE = "82 8A CA BC 06 01 01 66"  # mNpY module's FactoryReset
I2C_MESSAGE_AUTO_RETRY = 3     # wifiModuleAutoRetry - this call DOES retry,
I2C_MESSAGE_WAIT = 0.1          # msBetweenCalls - unlike block uploads


class BlockUploadFailed(Exception):
    """Raised by send_blocks; carries whether block 0 itself failed and
    whether the failure looks like a no-response/connection-level error."""
    def __init__(self, cause, failed_on_first_block, looks_like_unknown_error):
        super().__init__(str(cause))
        self.cause = cause
        self.failed_on_first_block = failed_on_first_block
        self.looks_like_unknown_error = looks_like_unknown_error


def http_get(host, path, timeout=10):
    req = urllib.request.Request(f"http://{host}{path}",
                                  headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_post_raw(host, path, data, timeout):
    req = urllib.request.Request(
        f"http://{host}{path}",
        data=data,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/octet-stream",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_post_json(host, path, obj, timeout):
    body = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(
        f"http://{host}{path}",
        data=body,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def is_no_response_error(exc):
    """True for failures with no real HTTP response from the device (the
    closest Python equivalent to the browser's 'Unknown error'/isTrusted
    signal) - connection refused, timeout, no route. False for an
    urllib.error.HTTPError, which carries a real status code the device
    returned, since the source only retries the "Unknown error" case, not a
    real HTTP error status."""
    if isinstance(exc, urllib.error.HTTPError):
        return False
    return isinstance(exc, (urllib.error.URLError, OSError, TimeoutError))


def check_not_already_updating(host, address, eeprom, dry_run):
    sub = "eeprom" if eeprom else "firmware"
    path = f"/i2c-appliance/{address}/{sub}/status"
    if dry_run:
        print(f"[dry-run] would GET {path}")
        return
    try:
        resp = http_get(host, path).decode(errors="replace")
    except Exception as e:
        print(f"WARNING: could not fetch {sub} status: {e}", file=sys.stderr)
        return
    print(f"{sub} status:", resp)
    if "updating" in resp.lower():
        print("ERROR: appliance reports an update already in progress - aborting, "
              "same as the app does.", file=sys.stderr)
        sys.exit(1)


def send_blocks(host, address, eeprom, data, dry_run):
    sub = "eeprom" if eeprom else "firmware"
    total = (len(data) + UPLOAD_SIZE - 1) // UPLOAD_SIZE
    offset = 0
    block_no = 0
    while True:
        remaining = len(data) - offset
        size = UPLOAD_SIZE if remaining > UPLOAD_SIZE else remaining
        block_no += 1
        print(f"[{sub}] block {block_no}/{total}  offset={offset}  size={size}  "
              f"progress={offset/len(data):.1%}")
        path = f"/i2c-appliance/{address}/{sub}?blockOffset={offset}&blockSize={size}"
        chunk = data[offset:offset + size]
        if dry_run:
            print(f"[dry-run] would POST {path} ({len(chunk)} bytes)")
        else:
            try:
                http_post_raw(host, path, chunk, BLOCK_TIMEOUT)
            except Exception as e:
                raise BlockUploadFailed(
                    e,
                    failed_on_first_block=(block_no == 1),
                    looks_like_unknown_error=is_no_response_error(e),
                ) from e
        offset += size
        if offset >= len(data):
            break
        time.sleep(WAIT_BETWEEN_CALLS)


def send_i2c_message(host, message, dry_run, attempt=0):
    """POST /i2c-bus/message {"message": ...}. Unlike block uploads, this
    call DOES retry in the source (sendCustomI2cMessage), up to
    I2C_MESSAGE_AUTO_RETRY times."""
    path = "/i2c-bus/message"
    body = {"message": message}
    print(f"i2c message -> {path}  body={body}  (attempt {attempt + 1}/{I2C_MESSAGE_AUTO_RETRY + 1})")
    if dry_run:
        print("[dry-run] would POST i2c-bus/message")
        return
    try:
        req_body = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"http://{host}{path}",
            data=req_body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Accept-Language": "en-EN",
            },
        )
        with urllib.request.urlopen(req, timeout=START_UPDATE_TIMEOUT) as r:
            resp = r.read()
        print("i2c message response:", resp.decode(errors="replace"))
    except Exception as e:
        print(f"i2c message attempt {attempt + 1} failed: {e}", file=sys.stderr)
        if attempt < I2C_MESSAGE_AUTO_RETRY:
            time.sleep(I2C_MESSAGE_WAIT)
            send_i2c_message(host, message, dry_run, attempt + 1)
        else:
            print("i2c message failed after all retries; the device may not have "
                  "been factory-reset. Check it manually.", file=sys.stderr)


def factory_reset(host, dry_run):
    print()
    print("!!! Sending FactoryReset I2C command - this wipes the device's "
          "current settings back to factory defaults (matches the app's "
          "behaviour for this firmware, apply_factory_reset_after_update=true). !!!")
    send_i2c_message(host, FACTORY_RESET_MESSAGE, dry_run)


def start_update(host, address, eeprom, image_size, image_hash, dry_run):
    sub = "eeprom" if eeprom else "firmware"
    path = f"/i2c-appliance/{address}/{sub}/start-update"
    body = {"imageSize": image_size, "imageHash": image_hash}
    print(f"start-update -> {path}  body={body}")
    if dry_run:
        print("[dry-run] would POST start-update")
        return
    try:
        resp = http_post_json(host, path, body, START_UPDATE_TIMEOUT)
        print("start-update response:", resp.decode(errors="replace"))
    except Exception as e:
        print(f"start-update call failed: {e}", file=sys.stderr)


def run_phase(host, address, eeprom, data, image_hash, dry_run, restarts=0):
    """One full main-or-eeprom phase, with the whole-phase restart behaviour
    from step_5_updateFirmware (see module docstring). Raises on final,
    non-retried failure."""
    label = "EEPROM" if eeprom else "main"
    print(f"=== {label} phase (attempt {restarts + 1}/{MAX_PHASE_RESTARTS + 1}) ===")

    check_not_already_updating(host, address, eeprom, dry_run)

    try:
        send_blocks(host, address, eeprom, data, dry_run)
    except BlockUploadFailed as e:
        print(f"[{label}] block upload failed: {e.cause}", file=sys.stderr)
        # Matches the source's catch path: a single best-effort start-update
        # before deciding whether to restart or give up.
        start_update(host, address, eeprom, len(data), image_hash, dry_run)

        should_restart = e.failed_on_first_block or e.looks_like_unknown_error
        if should_restart and restarts < MAX_PHASE_RESTARTS:
            print(f"[{label}] retrying whole phase from byte 0 "
                  f"({'failed on first block' if e.failed_on_first_block else 'no response from device'})...",
                  file=sys.stderr)
            run_phase(host, address, eeprom, data, image_hash, dry_run, restarts + 1)
            return
        raise SystemExit(f"[{label}] update failed, not retrying further: {e.cause}") from e

    start_update(host, address, eeprom, len(data), image_hash, dry_run)
    print(f"[{label}] waiting {SECOND_START_UPDATE_DELAY}s before firing start-update "
          f"a second time (the app does this unconditionally on success)...")
    if not dry_run:
        time.sleep(SECOND_START_UPDATE_DELAY)
    start_update(host, address, eeprom, len(data), image_hash, dry_run)
    print(f"[{label}] phase complete.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("firmware_bin", help="path to the downloaded base .bin firmware file")
    ap.add_argument("--eeprom-file",
                     help="path to the EEPROM .bin, if this firmware has one. When given, "
                          "the EEPROM phase runs automatically right after the main phase "
                          "succeeds, same as the app's has_eeprom chaining.")
    ap.add_argument("--host", default="192.168.4.1")
    ap.add_argument("--address", default="82",
                     help="appliance address as used in the URL path (default: 82, "
                          "same as the app's standardAppliance)")
    ap.add_argument("--image-hash", required=True,
                     help="the hash_text value from the firmware metadata the server "
                          "returned when you fetched/downloaded this firmware. Reused "
                          "for both the main and EEPROM phases, matching the source. "
                          "Pass an empty string if you genuinely don't have it.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-factory-reset", action="store_true",
                     help="Do NOT send the FactoryReset I2C command after a successful "
                          "update. By default this script sends it, matching the app's "
                          "behaviour for firmware with apply_factory_reset_after_update=true "
                          "(true for this HRU400 BASE v8.1 firmware). This WIPES the "
                          "device's current settings back to factory defaults.")
    args = ap.parse_args()

    with open(args.firmware_bin, "rb") as f:
        data = f.read()
    print(f"loaded {args.firmware_bin}: {len(data)} bytes")

    run_phase(args.host, args.address, False, data, args.image_hash, args.dry_run)

    if args.eeprom_file:
        with open(args.eeprom_file, "rb") as f:
            eeprom_data = f.read()
        print(f"loaded {args.eeprom_file}: {len(eeprom_data)} bytes (EEPROM)")
        run_phase(args.host, args.address, True, eeprom_data, args.image_hash, args.dry_run)

    # after_update(): fires once, after ALL phases succeed - not per phase.
    if args.skip_factory_reset:
        print("--skip-factory-reset given: not sending FactoryReset (app would send it "
              "automatically here for this firmware).")
    else:
        factory_reset(args.host, args.dry_run)

    print("done. Do not disconnect power from the appliance.")


if __name__ == "__main__":
    main()
