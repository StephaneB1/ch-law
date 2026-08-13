"""Step 2 — download the English XML named by the census.

Kept separate from parse.py on purpose: parse.py must run with no network at all,
so that the golden-file tests assert byte-for-byte determinism against committed
input. This stage is the only one that touches the filestore.

Files land at raw/acts/<sr>/<consolidated-en>/en.xml and are written verbatim —
no reformatting, no encoding fixes. An act already on disk at the same
consolidation date is left alone, so re-running is cheap and cannot rewrite
history: a new consolidation gets a new directory beside the old one.

Usage:
    python src/fetch.py [SR ...]      # default: every act in the census
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fedlex import USER_AGENT  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CENSUS_PATH = ROOT / "data" / "census.json"
ACTS_DIR = ROOT / "raw" / "acts"


def destination(act: dict) -> pathlib.Path:
    return ACTS_DIR / act["sr"] / act["consolidated_en"] / "en.xml"


def download(url: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()

    # The filestore fails soft the same way the SPARQL endpoint does: an error page
    # arrives as HTTP 200. Anything that is not XML must not reach raw/.
    head = body[:400].lstrip()
    if not head.startswith(b"<?xml") and not head.startswith(b"<akomaNtoso"):
        raise RuntimeError(f"not XML — body starts {head[:80]!r}")
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sr", nargs="*", help="SR numbers to fetch; default all")
    args = parser.parse_args()

    census = json.loads(CENSUS_PATH.read_text(encoding="utf-8"))
    by_sr = {a["sr"]: a for a in census["acts"]}

    wanted = args.sr or sorted(by_sr)
    unknown = [sr for sr in wanted if sr not in by_sr]
    if unknown:
        print(f"not in the census: {unknown}", file=sys.stderr)
        return 2

    fetched = skipped = 0
    for sr in wanted:
        act = by_sr[sr]
        path = destination(act)
        if path.exists():
            skipped += 1
            continue
        try:
            body = download(act["formats"]["xml"])
        except (OSError, RuntimeError) as exc:
            print(f"  FAIL {sr}: {exc}", file=sys.stderr)
            return 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        print(f"  {sr:<14} {len(body):>9,} bytes  {path.relative_to(ROOT)}")
        fetched += 1

    print(f"\n{fetched} fetched, {skipped} already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
