#!/usr/bin/env python3
"""
Standalone WiFi-module firmware flasher for ITHO Daalderop ventilation units.

Replicates the local update protocol used by the official "ITHO Daalderop
Service App" (apk-pretty/main.*.js: IWifiModuleService.wifimoduleFirmwareBlock
/ wifiModuleFirmwareApplyUpdate), plus the whole-process retry wrapped around
it in ApFirmwareUpdatePage.step_5_updateFirmware (13.f1220dcfea4e7bc3f73b.js).

This talks ONLY to the device's own local access point (default 192.168.4.1).
No vendor backend / account is involved - you must already have the .bin
firmware file downloaded.

Two behaviours, confirmed from source:

1. No retry per block (sendWifiFirmwareUpdateParts has no retry wrapper) - a
   failed block propagates straight up, and apply-update is still fired once
   as a best effort before giving up (handled in send_all_blocks()/main()).

2. A SEPARATE whole-process restart, up to 2 retries (3 attempts total),
   but ONLY when the failure is Angular's "Unknown error"/isTrusted signal -
   i.e. the WIFI branch of step_5_updateFirmware checks `statusText ===
   "Unknown error"` alone (unlike the appliance branch, it does NOT also
   retry on "failed on the very first block"). That signal only exists
   inside a browser's XHR stack; there's no literal Python equivalent. This
   script treats "no real HTTP response came back at all" (connection
   refused, timeout, no route - urllib.error.URLError without an HTTP status,
   or a socket timeout) as the equivalent, and does NOT restart on an
   HTTPError carrying a real status code from the device, matching the
   source's intent as closely as a different HTTP stack allows. Disclosed
   translation, not a literal match of something browser-only.

Usage:
    python3 flash_wifi_module.py firmware.bin
    python3 flash_wifi_module.py firmware.bin --host 192.168.4.1 --dry-run
"""
import argparse
import sys
import time
import urllib.request
import urllib.error

UPLOAD_SIZE = 8192          # this.uploadSize
WAIT_BETWEEN_CALLS = 0.1     # pacing between blocks only; NOT a retry delay -
                              # wifiModuleAutoRetry/msBetweenCalls in the source
                              # are wired into getIdentification/getVersion/
                              # getDataValues, NOT into the block-send path.
                              # See module docstring for the separate,
                              # whole-process restart that DOES exist above it.
BLOCK_TIMEOUT = 60           # the "6e4" ms timeout on the block POST
MAX_PROCESS_RESTARTS = 2     # step_5_updateFirmware's "n < 2" for the WIFI branch


class BlockUploadFailed(Exception):
    def __init__(self, cause, looks_like_unknown_error):
        super().__init__(str(cause))
        self.cause = cause
        self.looks_like_unknown_error = looks_like_unknown_error


def is_no_response_error(exc):
    """Closest Python equivalent to the browser's 'Unknown error'/isTrusted
    signal: no real HTTP response came back at all. An HTTPError (a real
    status code from the device) is deliberately excluded - the source only
    restarts on "Unknown error", not on a real HTTP error status."""
    if isinstance(exc, urllib.error.HTTPError):
        return False
    return isinstance(exc, (urllib.error.URLError, OSError, TimeoutError))


def http_get(host, path, timeout=10):
    url = f"http://{host}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_post_raw(host, path, data, timeout=BLOCK_TIMEOUT):
    url = f"http://{host}{path}"
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/octet-stream",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_post_empty(host, path, timeout=BLOCK_TIMEOUT):
    url = f"http://{host}{path}"
    req = urllib.request.Request(url, data=b"", method="POST",
                                  headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def send_block(host, offset, size, data, dry_run):
    chunk = data[offset:offset + size]
    path = f"/wifi-module/firmware/block?blockOffset={offset}&blockSize={size}"
    if dry_run:
        print(f"[dry-run] would POST {path} ({len(chunk)} bytes)")
        return
    # No retry here, by design: the real app does not retry a failed block
    # send within a single pass. See run_update() for the separate,
    # whole-process restart that wraps this.
    try:
        http_post_raw(host, path, chunk)
    except Exception as e:
        raise BlockUploadFailed(e, is_no_response_error(e)) from e


def send_all_blocks(host, data, dry_run):
    total = (len(data) + UPLOAD_SIZE - 1) // UPLOAD_SIZE
    offset = 0
    block_no = 0
    while True:
        remaining = len(data) - offset
        size = UPLOAD_SIZE if remaining > UPLOAD_SIZE else remaining
        block_no += 1
        print(f"block {block_no}/{total}  offset={offset}  size={size}  "
              f"progress={offset/len(data):.1%}")
        send_block(host, offset, size, data, dry_run)
        offset += size
        if offset >= len(data):
            break
        time.sleep(WAIT_BETWEEN_CALLS)


def apply_update(host, dry_run):
    print("applying update...")
    if dry_run:
        print("[dry-run] would POST /wifi-module/firmware/apply-update")
        return
    http_post_empty(host, "/wifi-module/firmware/apply-update")


def run_update(host, data, dry_run, restarts=0):
    print(f"=== update attempt {restarts + 1}/{MAX_PROCESS_RESTARTS + 1} ===")
    try:
        send_all_blocks(host, data, dry_run)
    except BlockUploadFailed as e:
        print(f"block upload failed: {e.cause}", file=sys.stderr)
        print("applying update anyway (matches app behaviour on error)...", file=sys.stderr)
        apply_update(host, dry_run)

        if e.looks_like_unknown_error and restarts < MAX_PROCESS_RESTARTS:
            print("retrying whole update from byte 0 (no response from device)...",
                  file=sys.stderr)
            run_update(host, data, dry_run, restarts + 1)
            return
        raise SystemExit(f"update failed, not retrying further: {e.cause}") from e

    apply_update(host, dry_run)
    print("done. Device should now be flashing/rebooting - do not disconnect power.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("firmware_bin", help="path to the downloaded .bin firmware file")
    ap.add_argument("--host", default="192.168.4.1",
                     help="device AP IP (default: 192.168.4.1, same as the app's "
                          "wifiModuleBasepath)")
    ap.add_argument("--dry-run", action="store_true",
                     help="print what would be sent, without touching the device")
    ap.add_argument("--skip-check", action="store_true",
                     help="skip the pre-flight /version and /identification GETs")
    args = ap.parse_args()

    with open(args.firmware_bin, "rb") as f:
        data = f.read()
    print(f"loaded {args.firmware_bin}: {len(data)} bytes")

    if not args.skip_check:
        try:
            ident = http_get(args.host, "/identification")
            ver = http_get(args.host, "/version")
            print("identification:", ident.decode(errors="replace"))
            print("current version:", ver.decode(errors="replace"))
        except Exception as e:
            print(f"WARNING: could not reach device at {args.host}: {e}", file=sys.stderr)
            print("Make sure your phone/laptop is joined to the device's own WiFi AP.",
                  file=sys.stderr)
            sys.exit(1)

    run_update(args.host, data, args.dry_run)


if __name__ == "__main__":
    main()
