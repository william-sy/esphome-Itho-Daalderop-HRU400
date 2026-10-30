#!/usr/bin/env python3
"""Keep firmware_metadata.json in step with the firmware files in base/ and wifi/.

Normally run through update_firmware.sh. Standalone:

    python3 update_metadata.py                      # record file + sha256 for every firmware file present
    python3 update_metadata.py --fetch              # also download missing files (public asset links)
    python3 update_metadata.py --catalog cat.json   # first merge new entries from a saved catalog response

Layout: BASE firmware lives in base/, WIFI firmware in wifi/, named hru400_<TYPE>_<version>.bin.
Entries are matched by their server "id". Existing entries keep every field; this only adds new
entries and fills in "file" (path relative to this folder) and "sha256". Standard library only.
"""
import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
META = os.path.join(HERE, "firmware_metadata.json")
SUBDIR = {"BASE": "base", "WIFI": "wifi"}      # also the only types the app can flash


def merge_catalog(meta, path):
    with open(path) as f:
        payload = json.load(f)
    product = payload.get("product", payload)
    have = {str(e["id"]) for e in meta["firmware"]}
    added = []
    for sv in product.get("software_versions", []):
        for fw_type, entries in (sv.get("firmware") or {}).items():
            if fw_type not in SUBDIR:
                continue
            for e in entries:
                if str(e.get("id")) in have:
                    continue
                meta["firmware"].append({"type": fw_type, **e})
                have.add(str(e["id"]))
                added.append(f"{fw_type} {e.get('firmware_version')} (id {e.get('id')})")
    return added


def fetch(entry, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(entry["url"]), timeout=90) as r:
        data = r.read()
        want = r.headers.get("Content-Length")
    if want and int(want) != len(data):
        raise IOError(f"short download: got {len(data)} of {want} bytes")
    with open(dest, "wb") as f:
        f.write(data)


def find_elsewhere(name):
    """Paths (relative to this folder) of files called `name` outside the canonical folders."""
    hits = []
    for root, _dirs, files in os.walk(HERE):
        if name in files:
            hits.append(os.path.relpath(os.path.join(root, name), HERE))
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true", help="download missing files from each entry's url")
    ap.add_argument("--catalog", help="saved catalog response to merge new entries from")
    args = ap.parse_args()

    with open(META) as f:
        meta = json.load(f)

    if args.catalog:
        added = merge_catalog(meta, args.catalog)
        print("new from catalog:", ", ".join(added) if added else "nothing new")

    listed = set()
    print(f"{'type':5} {'version':8} {'id':>4}  status")
    for e in meta["firmware"]:
        name = f"hru400_{e['type']}_{e['firmware_version']}.bin"
        rel = f"{SUBDIR.get(e['type'], '')}/{name}".lstrip("/")
        path = os.path.join(HERE, rel)
        status = ""
        if not os.path.exists(path) and args.fetch and e.get("url"):
            try:
                fetch(e, path)
                status = "downloaded, "
            except (urllib.error.URLError, IOError) as x:
                status = f"download failed ({x}), "
        if os.path.exists(path):
            with open(path, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
            changed = e.get("file") != rel or e.get("sha256") != digest
            e["file"], e["sha256"] = rel, digest
            listed.add(rel)
            status += f"{os.path.getsize(path)} bytes, sha256 {digest[:12]}" + (" (updated)" if changed else "")
        else:
            e.pop("file", None)
            e.pop("sha256", None)
            status += "no file"
            elsewhere = [p for p in find_elsewhere(name) if p != rel]
            if elsewhere:
                status += f"  <-- found at {', '.join(elsewhere)}, expected {rel}"
        print(f"{e['type']:5} {str(e['firmware_version']):8} {str(e['id']):>4}  {status}")

    for sub in SUBDIR.values():
        d = os.path.join(HERE, sub)
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            rel = f"{sub}/{name}"
            if rel not in listed and name.endswith(".bin"):
                print(f"note: {rel} is not listed in the metadata (wrong folder, or not a known version)")

    with open(META, "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    print("wrote", META)


if __name__ == "__main__":
    main()
