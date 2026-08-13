"""Shared Fedlex SPARQL access.

The endpoint fails soft: under load it answers 200 with an HTML error page instead
of SPARQL JSON. Every query therefore validates the response shape before handing
anything back, and retries a few times before giving up loudly.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

ENDPOINT = "https://fedlex.data.admin.ch/sparqlendpoint"

PREFIXES = """\
PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""

# jolux vocabulary terms we pin by URI rather than by label.
IN_FORCE = "https://fedlex.data.admin.ch/vocabulary/enforcement-status/0"
LANG_ENG = "http://publications.europa.eu/resource/authority/language/ENG"
FORMAT = "https://fedlex.data.admin.ch/vocabulary/user-format/"

USER_AGENT = "ch-law/0.1 (+https://github.com/stephanebottin/ch-law)"


class SparqlError(RuntimeError):
    """The endpoint returned something that is not a SPARQL result set."""


def query(sparql: str, *, timeout: int = 300, retries: int = 3) -> list[dict]:
    """Run a SPARQL SELECT and return its bindings.

    Raises SparqlError rather than returning partial or HTML content, so a failed
    nightly run cannot commit an empty census over a good one.
    """
    body = urllib.parse.urlencode({"query": PREFIXES + sparql}).encode()
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
    )

    last = ""
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                content_type = response.headers.get("Content-Type", "")
        except OSError as exc:  # network error, DNS, timeout
            last = f"{type(exc).__name__}: {exc}"
        else:
            if "json" not in content_type.lower():
                last = f"content-type {content_type!r}, body starts {raw[:80]!r}"
            else:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as exc:
                    last = f"invalid JSON: {exc}"
                else:
                    if "results" not in payload or "bindings" not in payload["results"]:
                        last = f"no result bindings in payload: {raw[:120]!r}"
                    else:
                        return payload["results"]["bindings"]

        if attempt < retries:
            time.sleep(2 * attempt)

    raise SparqlError(f"SPARQL query failed after {retries} attempts — {last}")


def value(binding: dict, name: str, default: str | None = None) -> str | None:
    """Read one variable out of a result binding."""
    cell = binding.get(name)
    return cell["value"] if cell else default
