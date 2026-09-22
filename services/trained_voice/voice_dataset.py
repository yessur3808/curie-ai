#!/usr/bin/env python3
"""Fetch only Curie's original WAVs from a public archive, never its checkpoints."""

import argparse
import concurrent.futures
import hashlib
import io
import json
import pathlib
import struct
import time
import urllib.parse
import urllib.request
import wave
import zlib

ARCHIVE = (
    "English/F4_F_NPCFCurie_Eng (Training Data)/F4_F_NPCFCurie_Eng (Training Data).zip"
)
PREFIX = "dataset/44k/F4_F_NPCFCurie_Eng/"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    out = pathlib.Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)
    with urllib.request.urlopen(
        "https://huggingface.co/api/datasets/Rootreck/Fallout_4", timeout=30
    ) as response:
        revision = json.load(response)["sha"]
    url = (
        "https://huggingface.co/datasets/Rootreck/Fallout_4/resolve/"
        + revision
        + "/"
        + urllib.parse.quote(ARCHIVE)
    )
    records = [
        r
        for r in json.loads(pathlib.Path(args.index).read_text())
        if r["name"].startswith(PREFIX) and r["name"].endswith(".wav")
    ]

    def fetch(record):
        path = out / pathlib.Path(record["name"]).name
        if not path.exists():
            start = record["offset"]
            length = record["compressed"] + 1024
            for attempt in range(4):
                try:
                    request = urllib.request.Request(
                        url + "?start=" + str(start),
                        headers={"Range": f"bytes={start}-{start+length-1}"},
                    )
                    with urllib.request.urlopen(request, timeout=50) as response:
                        if response.status != 206 or not response.headers.get(
                            "Content-Range", ""
                        ).startswith(f"bytes {start}-"):
                            raise ValueError("Archive server did not honor byte range")
                        raw = response.read(length + 1)
                    header = struct.unpack("<4s5H3I2H", raw[:30])
                    if header[0] != b"PK\x03\x04" or header[3] != 8:
                        raise ValueError("Unexpected archive header")
                    name_len, extra_len = header[-2:]
                    name = raw[30 : 30 + name_len].decode()
                    if name != record["name"]:
                        raise ValueError("Archive index does not match pinned source")
                    offset = 30 + name_len + extra_len
                    audio = zlib.decompress(
                        raw[offset : offset + record["compressed"]], -15
                    )
                    if len(audio) != record["bytes"] or (
                        header[6] and zlib.crc32(audio) != header[6]
                    ):
                        raise ValueError("Archive audio checksum mismatch")
                    path.write_bytes(audio)
                    path.chmod(0o600)
                    break
                except Exception:
                    if attempt == 3:
                        raise
                    time.sleep(attempt + 1)
        audio = path.read_bytes()
        with wave.open(io.BytesIO(audio)) as wav:
            seconds = wav.getnframes() / wav.getframerate()
            rate = wav.getframerate()
        return {
            "id": path.stem,
            "speaker": "fallout4-curie",
            "audio": path.name,
            "seconds": seconds,
            "sampleRate": rate,
            "sha256": hashlib.sha256(audio).hexdigest(),
            "archiveEntry": record["name"],
        }

    manifest = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for row in pool.map(fetch, records):
            manifest.append(row)
            if len(manifest) % 25 == 0:
                print(
                    json.dumps({"downloaded": len(manifest), "total": len(records)}),
                    flush=True,
                )
    result = {
        "source": "https://huggingface.co/datasets/Rootreck/Fallout_4",
        "revision": revision,
        "license": "No dataset license declared by uploader; original game/performance rights are not transferred. Private experiment; no audio redistribution.",
        "variant": "original; enhanced and equalized duplicate versions excluded",
        "records": manifest,
    }
    (out / "manifest.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {
                "clips": len(manifest),
                "minutes": round(sum(r["seconds"] for r in manifest) / 60, 1),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
