"""Install upstream archives only after verifying a pinned cryptographic hash."""
import hashlib
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request


def main(url, digest, destination):
    if not url.startswith("https://") or not re.fullmatch(r"[0-9a-fA-F]{64}|[0-9a-fA-F]{128}", digest):
        raise ValueError("HTTPS URL and pinned SHA-256/SHA-512 required")
    target = Path(destination)
    with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
        root = Path(temporary)
        archive = root / "archive.tgz"
        checksum = hashlib.sha256() if len(digest) == 64 else hashlib.sha512()
        with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                checksum.update(chunk)
                stream.write(chunk)
        if checksum.hexdigest() != digest.lower():
            raise ValueError("Archive checksum mismatch")
        unpacked = root / "unpacked"
        unpacked.mkdir()
        with tarfile.open(archive) as source:
            source.extractall(unpacked, filter="data")
        entries = list(unpacked.iterdir())
        if len(entries) != 1 or not entries[0].is_dir():
            raise ValueError("Expected a single archive root")
        shutil.move(str(entries[0]), target)


if __name__ == "__main__":
    main(*sys.argv[1:])
