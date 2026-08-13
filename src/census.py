"""Step 1 — census of Swiss federal acts available in official English.

Emits data/census.json: one record per in-force SR act that has a retrievable
English consolidated XML on Fedlex. That set — 258 acts, not the 3,274 that merely
have an English *expression* — is the scope of the whole pipeline.

Two subtleties drive the selection logic:

  * The English consolidation is often not the latest consolidation. SR 311.0 has
    consolidations newer than its newest English text. Choosing the latest
    consolidation and then looking for English silently drops such acts, so we pick
    the latest consolidation *that has English XML* and record the current-text date
    separately.

  * Aggregating in SPARQL is not trustworthy here: GROUP BY on the SR notation
    returned dates that per-act queries contradicted. We pull raw rows and
    aggregate in Python, keyed on the act's ELI URI.

Usage:
    python src/census.py [--as-of YYYY-MM-DD] [--check]
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fedlex import FORMAT, IN_FORCE, LANG_ENG, SparqlError, query, value  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CENSUS_PATH = ROOT / "data" / "census.json"
SKIPPED_PATH = ROOT / "data" / "skipped.json"
RAW_DIR = ROOT / "raw" / "sparql"

# Acts whose English consolidation carries a manifestation, with the download URL
# per format. One row per (act, consolidation, format) — collapsed in Python.
#
# isExemplifiedBy is OPTIONAL on purpose: SR 935.121.42 is in force and advertises
# an English XML manifestation that has no download URL at all. Requiring the URL
# here would drop it without trace; instead we let it through and skip it by name.
ENGLISH_ROWS = """\
SELECT DISTINCT ?act ?sr ?date ?format ?url WHERE {
  ?act a jolux:ConsolidationAbstract ;
       jolux:inForceStatus <%(in_force)s> ;
       jolux:classifiedByTaxonomyEntry/skos:notation ?sr .
  ?cons jolux:isMemberOf ?act ;
        jolux:dateApplicability ?date ;
        jolux:isRealizedBy ?expr .
  FILTER(?date <= "%(as_of)s"^^xsd:date)
  ?expr jolux:language <%(lang)s> ;
        jolux:isEmbodiedBy ?mani .
  ?mani jolux:userFormat ?format .
  OPTIONAL { ?mani jolux:isExemplifiedBy ?url }
}
"""

# Latest consolidation in any language, to detect English lagging the binding text.
CURRENT_ROWS = """\
SELECT DISTINCT ?act ?date WHERE {
  ?act a jolux:ConsolidationAbstract ;
       jolux:inForceStatus <%(in_force)s> ;
       jolux:classifiedByTaxonomyEntry/skos:notation ?sr .
  ?cons jolux:isMemberOf ?act ; jolux:dateApplicability ?date .
  FILTER(?date <= "%(as_of)s"^^xsd:date)
}
"""

# Titles hang off the act's own expressions, not off each consolidation's — the
# consolidation expressions carry no jolux:title at all.
TITLE_ROWS = """\
SELECT DISTINCT ?act ?title ?alt ?abbr WHERE {
  ?act a jolux:ConsolidationAbstract ;
       jolux:inForceStatus <%(in_force)s> ;
       jolux:classifiedByTaxonomyEntry/skos:notation ?sr ;
       jolux:isRealizedBy ?expr .
  ?expr jolux:language <%(lang)s> .
  OPTIONAL { ?expr jolux:title ?title }
  OPTIONAL { ?expr jolux:titleAlternative ?alt }
  OPTIONAL { ?expr jolux:titleShort ?abbr }
  FILTER(BOUND(?title) || BOUND(?alt) || BOUND(?abbr))
}
"""

# The facts behind the slate's attribute table: Document Type, Date In Force, and
# the adoption date. All three hang off the act itself, not off any consolidation,
# so they are the same whichever language you read it in.
FACTS_ROWS = """\
SELECT DISTINCT ?act ?type ?adopted ?inforce WHERE {
  ?act a jolux:ConsolidationAbstract ;
       jolux:inForceStatus <%(in_force)s> ;
       jolux:classifiedByTaxonomyEntry/skos:notation ?sr .
  OPTIONAL { ?act jolux:typeDocument ?type }
  OPTIONAL { ?act jolux:dateDocument ?adopted }
  OPTIONAL { ?act jolux:dateEntryInForce ?inforce }
}
"""

# English names for the document types encountered. Unlike the classification tree,
# this vocabulary is fully translated: all 11 types in the English corpus have an
# English prefLabel, so no hand-written fallback file is needed. Labels are taken
# verbatim — "Federal act", not "Federal Act" — because inventing a nicer casing is
# still inventing.
TYPE_LABELS = """\
SELECT ?type ?label WHERE {
  VALUES ?type { %(types)s }
  ?type skos:prefLabel ?label .
  FILTER(lang(?label) = "en")
}
"""

# Every act in force, to report how much of the corpus English covers.
ALL_ACTS = """\
SELECT DISTINCT ?act ?sr WHERE {
  ?act a jolux:ConsolidationAbstract ;
       jolux:inForceStatus <%(in_force)s> ;
       jolux:classifiedByTaxonomyEntry/skos:notation ?sr .
}
"""

# Facts established by direct per-act inspection of the graph. They encode real
# selection bugs, so a regression must fail the build rather than ship quietly.
GATES_PRESENT = {
    "311.0": "2026-01-01",  # English lags newer DE/FR/IT consolidations
    "231.1": "2025-07-01",
    "220": "2026-01-01",
    "210": "2026-07-01",
}
GATES_ABSENT = ["741.01", "642.11"]  # no English at all — must skip cleanly

# The attribute table of the README's worked example, pinned end to end.
GATES_ATTRIBUTES = {
    "231.1": {"document_type": "Federal act", "adopted": "1992-10-09", "in_force": "1993-07-01"},
}


def short_format(uri: str) -> str:
    return uri[len(FORMAT):] if uri.startswith(FORMAT) else uri


def sr_class(sr: str) -> str:
    return sr.split(".")[0][0] if sr else "?"


def one(values: set[str]) -> str | None:
    """The single value of a set, or None if the graph gave none or several.

    Ambiguity is reported as absence on purpose: an act with two entry-into-force
    dates has no answer to "Date In Force", and check() fails on the null rather
    than the emitter picking one.
    """
    return next(iter(values)) if len(values) == 1 else None


def build(as_of: str) -> dict:
    params = {"in_force": IN_FORCE, "lang": LANG_ENG, "as_of": as_of}

    english = query(ENGLISH_ROWS % params)
    current = query(CURRENT_ROWS % params)
    titles = query(TITLE_ROWS % params)
    everything = query(ALL_ACTS % params)
    facts = query(FACTS_ROWS % params)

    per_act_facts: dict[str, dict[str, set]] = collections.defaultdict(
        lambda: collections.defaultdict(set)
    )
    for row in facts:
        act = per_act_facts[value(row, "act")]
        for key in ("type", "adopted", "inforce"):
            found = value(row, key)
            if found:
                act[key].add(found)

    type_uris = {one(f["type"]) for f in per_act_facts.values()} - {None}
    type_labels = {
        value(row, "type"): value(row, "label")
        for row in query(TYPE_LABELS % {"types": " ".join(f"<{u}>" for u in sorted(type_uris))})
    }

    # Collapse (act, consolidation date) -> {format: url}.
    per_act: dict[str, dict] = collections.defaultdict(
        lambda: {"sr": set(), "dates": collections.defaultdict(dict)}
    )
    for row in english:
        act = value(row, "act")
        entry = per_act[act]
        entry["sr"].add(value(row, "sr"))
        entry["dates"][value(row, "date")][short_format(value(row, "format"))] = value(row, "url")

    latest_any: dict[str, str] = {}
    for row in current:
        act, date = value(row, "act"), value(row, "date")
        if date > latest_any.get(act, ""):
            latest_any[act] = date

    # An act can carry several English expressions (superseded ones keep their
    # titles). Pick the fullest candidate, breaking ties lexicographically so the
    # choice does not depend on row order.
    candidates: dict[str, dict[str, set]] = collections.defaultdict(
        lambda: collections.defaultdict(set)
    )
    for row in titles:
        act = value(row, "act")
        for key, var in (("title", "title"), ("short_title", "alt"), ("abbreviation", "abbr")):
            found = value(row, var)
            if found:
                candidates[act][key].add(found)

    naming = {
        act: {key: max(sorted(vals), key=len) for key, vals in fields.items()}
        for act, fields in candidates.items()
    }

    acts, skipped, collisions = [], [], []
    for act, entry in per_act.items():
        # An act with two SR numbers would make the file layout ambiguous.
        if len(entry["sr"]) != 1:
            collisions.append((act, sorted(entry["sr"])))
            continue
        sr = next(iter(entry["sr"]))

        # The English text is whatever the newest consolidation carrying English
        # XML says — not the newest consolidation.
        with_xml = {d: f for d, f in entry["dates"].items() if "xml" in f}
        if not with_xml:
            # Overwhelmingly class-0 treaties: an English expression exists as a
            # metadata record, but no document was ever published for it.
            skipped.append({"sr": sr, "eli": act, "reason": "no-english-xml"})
            continue
        en_date = max(with_xml)

        if not with_xml[en_date].get("xml"):
            skipped.append(
                {
                    "sr": sr,
                    "eli": act,
                    "reason": "no-download-url",
                    "consolidated_en": en_date,
                }
            )
            continue

        # No English title metadata is a reliable tell that the "English"
        # expression is not English. SR 520.31 is the only case: it advertises an
        # English XML whose document body is Italian. Emitting it would put
        # non-English legal text into an English mirror.
        names = naming.get(act, {})
        if not names.get("title"):
            skipped.append(
                {
                    "sr": sr,
                    "eli": act,
                    "reason": "no-english-title",
                    "consolidated_en": en_date,
                    "note": "no English title metadata; document body is not English",
                }
            )
            continue

        current_date = latest_any.get(act, en_date)
        about = per_act_facts.get(act, {})
        type_uri = one(about.get("type", set()))

        acts.append(
            {
                "sr": sr,
                "title": names.get("title"),
                "short_title": names.get("short_title"),
                "abbreviation": names.get("abbreviation"),
                "eli": act,
                "document_type": type_labels.get(type_uri),
                "document_type_uri": type_uri,
                "adopted": one(about.get("adopted", set())),
                "in_force": one(about.get("inforce", set())),
                "consolidated_en": en_date,
                "consolidated_current": current_date,
                "stale": current_date > en_date,
                "formats": {k: v for k, v in sorted(with_xml[en_date].items()) if v},
            }
        )

    if collisions:
        raise SystemExit(f"acts carrying multiple SR numbers: {collisions}")

    def sr_key(sr: str) -> list:
        return [int(p) if p.isdigit() else p for p in sr.split(".")]

    acts.sort(key=lambda a: sr_key(a["sr"]))
    skipped.sort(key=lambda s: sr_key(s["sr"]))

    in_force_total = len({value(r, "act") for r in everything})
    return {
        "as_of": as_of,
        "source": "https://fedlex.data.admin.ch/sparqlendpoint",
        "in_force_total": in_force_total,
        "english_total": len(acts),
        "acts": acts,
    }, skipped


def report(census: dict, skipped: list[dict]) -> None:
    acts = census["acts"]
    print(f"as of {census['as_of']}")
    print(f"  in-force SR acts             {census['in_force_total']:>5}")
    print(f"  with English consolidated XML{len(acts):>5}")

    formats = collections.Counter(f for a in acts for f in a["formats"])
    print("\nby manifestation format")
    for name, count in formats.most_common():
        print(f"  {name:<8} {count:>5}")

    classes = collections.Counter(sr_class(a["sr"]) for a in acts)
    print("\nby SR class")
    for name in sorted(classes):
        print(f"  {name:<8} {classes[name]:>5}")

    kinds = collections.Counter(a["document_type"] for a in acts)
    print("\nby document type")
    for name, count in kinds.most_common():
        print(f"  {str(name):<48} {count:>5}")

    stale = [a for a in acts if a["stale"]]
    print(f"\nEnglish behind the binding text: {len(stale)} of {len(acts)}")
    for act in sorted(stale, key=lambda a: a["consolidated_en"])[:5]:
        print(f"  {act['sr']:<14} en {act['consolidated_en']}  current {act['consolidated_current']}")
    if len(stale) > 5:
        print(f"  … and {len(stale) - 5} more")

    reasons = collections.Counter(s["reason"] for s in skipped)
    print("\nskipped")
    for reason, count in sorted(reasons.items()):
        print(f"  {reason:<16} {count:>5}")
    notes = {
        "no-download-url": "English XML advertised but no file published",
        "no-english-title": "no English title metadata; document body is not English",
    }
    for entry in skipped:
        if entry["reason"] in notes:
            print(f"    {entry['sr']:<14} {notes[entry['reason']]}")


def check(census: dict, skipped: list[dict]) -> int:
    by_sr = {a["sr"]: a for a in census["acts"]}
    failures = []

    # An act must never vanish without a recorded reason.
    by_reason = collections.defaultdict(set)
    for entry in skipped:
        by_reason[entry["reason"]].add(entry["sr"])
    for sr, reason in (("935.121.42", "no-download-url"), ("520.31", "no-english-title")):
        if sr not in by_reason[reason]:
            failures.append(f"{sr} should be skipped as {reason}")

    for sr, expected in GATES_PRESENT.items():
        act = by_sr.get(sr)
        if act is None:
            failures.append(f"{sr} missing — expected English at {expected}")
        elif act["consolidated_en"] != expected:
            failures.append(f"{sr} English is {act['consolidated_en']}, expected {expected}")

    for sr in GATES_ABSENT:
        if sr in by_sr:
            failures.append(f"{sr} present but has no official English translation")

    for act in census["acts"]:
        if "xml" not in act["formats"]:
            failures.append(f"{act['sr']} has no XML manifestation")

    # Document Type, Date In Force and the adoption date are mandatory on every
    # slate, so a null here has to stop the build rather than emit a half table.
    for field in ("document_type", "adopted", "in_force"):
        blank = [a["sr"] for a in census["acts"] if not a[field]]
        if blank:
            failures.append(f"{len(blank)} act(s) without {field}: {blank[:5]}")

    for sr, expected in GATES_ATTRIBUTES.items():
        act = by_sr.get(sr, {})
        for field, want in expected.items():
            if act.get(field) != want:
                failures.append(f"{sr} {field} is {act.get(field)!r}, expected {want!r}")

    untitled = [a["sr"] for a in census["acts"] if not a["title"]]
    if untitled:
        failures.append(f"{len(untitled)} act(s) without an English title: {untitled[:5]}")

    if len(by_sr) != len(census["acts"]):
        failures.append("two acts claim the same SR number")

    for line in failures:
        print(f"  FAIL {line}")
    print(f"\n{'FAILED' if failures else 'ok'} — {len(failures)} problem(s)")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--as-of",
        default=dt.date.today().isoformat(),
        help="consolidation cutoff date; output is reproducible for a given value",
    )
    parser.add_argument("--check", action="store_true", help="run validation gates")
    args = parser.parse_args()

    try:
        census, skipped = build(args.as_of)
    except SparqlError as exc:
        print(f"census aborted: {exc}", file=sys.stderr)
        return 2

    CENSUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    def dump(path: pathlib.Path, payload) -> str:
        text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        path.write_text(text, encoding="utf-8")
        return text

    text = dump(CENSUS_PATH, census)
    (RAW_DIR / f"census-{args.as_of}.json").write_text(text, encoding="utf-8")
    dump(SKIPPED_PATH, {"as_of": args.as_of, "skipped": skipped})

    report(census, skipped)
    print(f"\nwrote {CENSUS_PATH.relative_to(ROOT)} and {SKIPPED_PATH.relative_to(ROOT)}")
    return check(census, skipped) if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
