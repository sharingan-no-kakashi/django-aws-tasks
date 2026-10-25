"""Run after publication to download the released distributions from PyPI.
Compare their hashes with the tested candidate to verify the published bytes.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import urlopen


def public_release(candidate):
    project = quote(candidate["project"], safe="")
    version = quote(candidate["version"], safe="")
    url = f"https://pypi.org/pypi/{project}/{version}/json"
    deadline = time.monotonic() + 300
    while True:
        try:
            with urlopen(url, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code != 404 or time.monotonic() >= deadline:
                raise
            time.sleep(5)


def download(candidate, destination):
    published = public_release(candidate)
    files = {row["filename"]: row for row in published["urls"]}
    destination.mkdir(parents=True, exist_ok=True)
    for name, expected in candidate["artifacts"].items():
        row = files[name]
        if row["digests"]["sha256"] != expected:
            raise ValueError("PyPI artifact metadata differs from the candidate")
        url = urlparse(row["url"])
        if url.scheme != "https" or url.hostname != "files.pythonhosted.org":
            raise ValueError("Unexpected artifact host in PyPI metadata")
        with urlopen(row["url"], timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != expected or Path(name).name != name:
            raise ValueError("Downloaded public artifact differs from the candidate")
        (destination / name).write_bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    options = parser.parse_args()
    download(json.loads(options.candidate.read_text()), options.destination)


if __name__ == "__main__":
    main()
