"""Step 3 — Akoma Ntoso XML → a slate.

Reads only committed files: data/census.json for the metadata and
raw/acts/<sr>/<date>/en.xml for the text. No network, so the output is a pure
function of what is in the repository.

The chext format has no chip for long-form prose and no anchor key, so an article
is carried by an :INFO chip: line 0 is its number and heading, the remaining lines
are its paragraphs. Structural headings below page level (Chapter, Section) become
:PATH chips, which render as a divider rather than as a card.

Usage:
    python src/parse.py SR [SR ...]
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
CENSUS_PATH = ROOT / "data" / "census.json"
ACTS_DIR = ROOT / "raw" / "acts"
SLATES_DIR = ROOT / "slates"

AKN = "{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}"

# Elements that divide an act above article level, in the order Fedlex nests them.
# Only these open a page; anything else that holds articles is still walked into.
DIVISIONS = ("book", "part", "title", "chapter", "section", "subsection", "subchapter")

# Elements that carry an article's own text rather than structure.
CONTENT = ("content", "p", "blockList", "paragraph", "intro", "wrapUp", "table")

# Metadata on a container, read by labelled() rather than walked into.
LABELS = ("num", "heading", "subheading", "authorialNote")

MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

# Fedlex states the disclaimer itself, in the document's own preface. Using its
# wording rather than one of ours keeps the slate a mirror; this constant is only
# the floor if a document ever omits it.
DISCLAIMER = (
    "English is not an official language of the Swiss Confederation. This "
    "translation is provided for information purposes only and has no legal force."
)

# The adoption date inside a Fedlex title ("Federal Constitution of 18 April 1999
# of the Swiss Confederation"). It is dropped only when the full title will not fit
# the 50-character name limit — the date is carried in the attribute table anyway.
TITLE_DATE = re.compile(r"\s+of\s+\d{1,2}\s+\w+\s+\d{4}")

NAME_LIMIT = 50

# Elements that sit inside a run of text rather than breaking it. "Art. 305bis"
# is a <sup> inside a <num>, and the ordinal has to close up against the number.
INLINE = frozenset(
    ("sup", "sub", "b", "i", "u", "inline", "ref", "span", "docNumber", "docTitle", "date")
)


class ParseError(RuntimeError):
    """The document is not shaped the way the emitter assumes."""


def spoken_date(iso: str) -> str:
    """1992-10-09 -> 9 October 1992, the form Fedlex uses in its own titles."""
    year, month, day = (int(p) for p in iso.split("-"))
    return f"{day} {MONTHS[month - 1]} {year}"


def slate_name(act: dict) -> str:
    """The slate title, inside chext's 50-character limit.

    Fedlex titles are long — 94 of the 256 acts overshoot, and a hard cut loses the
    abbreviation at the end, which is the part a reader recognises ("Federal
    Constitution of 18 April 1999 of the Swiss| Confederation (Cst.)"). So try
    progressively shorter honest forms and only truncate when none of them fit.
    """
    title, short, abbr = act["title"], act["short_title"], act["abbreviation"]
    undated = TITLE_DATE.sub("", title, count=1)

    for base in (short, undated, title):
        if not base:
            continue
        if abbr and len(f"{base} ({abbr})") <= NAME_LIMIT:
            return f"{base} ({abbr})"
        if len(base) <= NAME_LIMIT:
            return base
    if abbr:
        return abbr[:NAME_LIMIT]

    # Ten acts reach here: long treaty titles with neither a short title nor an
    # abbreviation. Cut on a word boundary so the name reads as a phrase.
    cut = undated[: NAME_LIMIT - 1].rsplit(" ", 1)[0]
    return f"{cut}…"


def eid_label(eid: str) -> str:
    """art_2/para_3_bis -> 'Para. 3bis' — what a footnote is attached to."""
    parts = eid.split("/")
    if len(parts) == 1:
        return f"Art. {parts[0].removeprefix('art_').replace('_', '')}"

    spoken = []
    for part in parts[1:]:
        if part.startswith("para_"):
            spoken.append(f"Para. {part.removeprefix('para_').replace('_', '')}")
        elif part.startswith("lbl_"):
            spoken.append(f"let. {part.removeprefix('lbl_').replace('_', '')}")
    return " ".join(spoken)


def slate_path(sr: str) -> pathlib.Path:
    """slates/<class>/<group>/<sr> — one directory per digit group.

    Class 0 is the international treaties, whose numbers carry the class in front
    of the dot: SR 0.810.3 belongs to group 0.81 exactly as SR 231.1 belongs to
    group 23. Same rule, read one component to the right.
    """
    if sr.startswith("0."):
        digits = sr.split(".")[1]
        return SLATES_DIR / "0" / f"0.{digits[:2]}" / sr
    digits = sr.split(".")[0]
    return SLATES_DIR / digits[0] / digits[:2] / sr


def text_of(node: ET.Element) -> str:
    """Flattened text of an element, with footnotes left out.

    Runs of whitespace collapse to one space, and that includes U+00A0. Fedlex
    applies non-breaking spaces inconsistently — 12 of the 113 article numbers in
    SR 231.1 carry one, the rest do not — so keeping them verbatim reproduces a
    typesetting accident as if it were part of the law. Every other typographic
    character (curly quotes, dashes, ordinals) is left exactly as published.
    """
    parts: list[str] = []

    def walk(element: ET.Element) -> None:
        for child in element:
            if child.tag == f"{AKN}authorialNote":
                if child.tail:
                    parts.append(child.tail)
                continue
            if child.text:
                parts.append(child.text)
            walk(child)
            if child.tail:
                parts.append(child.tail)

    if node.text:
        parts.append(node.text)
    walk(node)
    return re.sub(r"[ \t\r\n ]+", " ", "".join(parts)).strip()


def notes_in(node: ET.Element) -> list[tuple[str, str]]:
    """Every footnote under a node, paired with what it is attached to.

    A footnote almost never applies to the whole article: of the 108 in SR 231.1,
    49 hang off a single paragraph and 23 off a single letter. Presenting them as
    one undifferentiated block says that Art. 2 was inserted in 2019 when only its
    paragraph 3bis was, so each note keeps the eId of its nearest anchor.
    """
    parents = {child: parent for parent in node.iter() for child in parent}
    found: list[tuple[str, str]] = []

    for note in node.iter(f"{AKN}authorialNote"):
        written = text_of(note)
        if not written:
            continue
        anchor = parents.get(note)
        while anchor is not None and not anchor.get("eId"):
            anchor = parents.get(anchor)
        eid = anchor.get("eId") if anchor is not None else None
        found.append((eid_label(eid) if eid else "", written))
    return found


def item_lines(item: ET.Element) -> list[str]:
    """A list item: its label and text on one line, then any list nested under it.

    Lists do nest — Art. 22c has a sub-list under item b — and a walker that only
    reads the item's own <p> children drops that text without a trace.
    """
    label = item.find(f"{AKN}num")
    spoken: list[str] = []
    nested: list[str] = []

    for child in item:
        tag = child.tag[len(AKN):]
        if tag == "num":
            continue
        if tag == "blockList":
            nested.extend(block_lines(child))
        else:
            written = text_of(child)
            if written:
                spoken.append(written)

    prefix = text_of(label) if label is not None else ""
    line = f"{prefix} {' '.join(spoken)}".strip()

    # An item whose whole body is a sub-list ("b. the work is one of the
    # following:") would otherwise leave its label stranded on a line of its own.
    if not spoken and nested:
        nested[0] = f"{prefix} {nested[0]}".strip()
        return nested
    return ([line] if line else []) + nested


def block_lines(block: ET.Element) -> list[str]:
    """A blockList: its introduction, then one line per item."""
    lines: list[str] = []
    intro = block.find(f"{AKN}listIntroduction")
    if intro is not None and text_of(intro):
        lines.append(text_of(intro))
    for item in block.findall(f"{AKN}item"):
        lines.extend(item_lines(item))
    return lines


def content_lines(content: ET.Element) -> list[str]:
    lines: list[str] = []
    for block in content:
        if block.tag == f"{AKN}blockList":
            lines.extend(block_lines(block))
        else:
            written = text_of(block)
            if written:
                lines.append(written)
    return lines


def one_paragraph(para: ET.Element) -> list[str]:
    """A numbered paragraph: its blocks, with the number leading the first line."""
    num = para.find(f"{AKN}num")
    marker = text_of(num) if num is not None else ""

    # Every child is read, not only <content>: SR 101 hangs tables straight off
    # the paragraph, and a walker that looked for <content> alone lost them.
    blocks: list[str] = []
    for child in para:
        tag = child.tag[len(AKN):]
        if tag in ("num", "authorialNote"):
            continue
        if tag == "content":
            blocks.extend(content_lines(child))
        elif tag == "blockList":
            blocks.extend(block_lines(child))
        else:
            written = text_of(child)
            if written:
                blocks.append(written)
    if not blocks:
        return []

    # The paragraph number leads its first line; Fedlex prints it as a
    # superscript, which chext has no way to express.
    blocks[0] = f"{marker} {blocks[0]}".strip() if marker else blocks[0]
    return blocks


def own_text(node: ET.Element) -> list[str]:
    """Text belonging to a node itself, outside any article beneath it.

    A <transitional> block carries its provisions as bare <paragraph> children,
    with no article in sight. Reading only articles loses every word of them.
    """
    lines: list[str] = []
    for element in node:
        tag = element.tag[len(AKN):]
        if tag == "paragraph":
            lines.extend(one_paragraph(element))
        elif tag == "content":
            lines.extend(content_lines(element))
        elif tag == "blockList":
            lines.extend(block_lines(element))
        elif tag in CONTENT:
            written = text_of(element)
            if written:
                lines.append(written)
    return lines


def nested_text(node: ET.Element) -> list[str]:
    """Structure nested inside an article, headings and all.

    The Federal Constitution keeps its transitional provisions as <subdivision>
    blocks under the article they amend, so an article is not always a flat list
    of paragraphs.
    """
    lines: list[str] = []
    for child in node:
        tag = child.tag[len(AKN):]
        if tag in CONTENT or tag in LABELS or tag == "article":
            continue
        label = labelled(child)
        if label:
            lines.append(label)
        subtitle = subheading_of(child)
        if subtitle:
            lines.append(subtitle)
        lines.extend(own_text(child))
        lines.extend(nested_text(child))
    return lines


def paragraph_lines(article: ET.Element) -> list[str]:
    """One chext line per paragraph, list item, or bare block of an article."""
    lines: list[str] = []

    for para in article.findall(f"{AKN}paragraph"):
        lines.extend(one_paragraph(para))

    # A few articles carry content directly, with no numbered paragraphs at all.
    if not lines:
        for content in article.findall(f"{AKN}content"):
            lines.extend(content_lines(content))
    return lines + nested_text(article)


def chip(marker: str, lines: list[str], group: str) -> tuple[str, list[str], str]:
    """One chip block, guarding the two ways chext would misread legal text.

    `group` decides connection: consecutive chips carrying the same group are
    joined with a trailing `|`, so a chapter reads as one connected run.
    """
    for line in lines:
        # A comment marker is whitespace-delimited — otherwise the format could
        # never carry a URL, and :LINK chips take one as their first line.
        if re.search(r"(^|\s)//", line):
            raise ParseError(f"'//' would be read as a comment: {line[:60]!r}")
        if line.endswith("\\"):
            raise ParseError(f"trailing backslash would join lines: {line[:60]!r}")
        if re.fullmatch(r":[A-Z]+(\.half)?( \[.*\])?", line):
            raise ParseError(f"line would be read as a chip marker: {line!r}")
        if line.strip() == "|":
            raise ParseError("a body line of '|' would be read as a connector")
    return (marker, lines, group)


def lay_out(blocks: list[tuple[str, list[str], str]]) -> list[str]:
    """Chip blocks to chext lines, connecting each group into one run.

    A bare `|` on the last line of a block ties it to the block below, so the
    chain breaks exactly where the group changes — at each divider, and between
    an act's structural sections.
    """
    out: list[str] = []
    for index, (marker, lines, group) in enumerate(blocks):
        following = blocks[index + 1][2] if index + 1 < len(blocks) else None
        out.extend([marker, *lines])
        if following == group:
            out.append("|")
        out.append("")
    return out


def preface_disclaimer(root: ET.Element) -> str:
    """Fedlex's own words about the translation, from the document's preface."""
    preface = root.find(f".//{AKN}preface")
    if preface is not None:
        for para in preface.findall(f"{AKN}p"):
            written = text_of(para)
            if "no legal force" in written:
                return written
    return DISCLAIMER


# Words a truncated slug should not end on. Fedlex headings are whole sentences
# ("Obligation of Providers of Internet Hosting Services which store Information
# entered by Users"), and a cut that lands on one reads as a mistake.
DANGLING = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or",
    "the", "to", "which", "who", "with",
}


def kebab(text: str, limit: int = 48) -> str:
    """A heading as a page slug — the file name becomes the page's visible name."""
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    slug = re.sub(r"[^A-Za-z0-9]+", "-", folded).strip("-").lower()

    if len(slug) > limit:
        words = slug[:limit].split("-")[:-1]
        while words and words[-1] in DANGLING:
            words.pop()
        slug = "-".join(words)
    return slug or "section"


def labelled(node: ET.Element) -> str:
    """'Title 1 Subject-Matter' — a division's number and heading."""
    num = node.find(f"{AKN}num")
    name = node.find(f"{AKN}heading")
    return " ".join(
        t for t in (
            text_of(num) if num is not None else "",
            text_of(name) if name is not None else "",
        ) if t
    )


def subheading_of(node: ET.Element) -> str:
    """An article's or division's subtitle.

    Fedlex uses it for the cross-reference under a heading — SR 951.311 puts
    "(Art. 2 para. 2 let. f CISA)" there — which is text of the act, not metadata,
    and reading only <num> and <heading> silently dropped 1,395 words of it.
    """
    element = node.find(f"{AKN}subheading")
    return text_of(element) if element is not None else ""


def heading_only(node: ET.Element) -> str:
    name = node.find(f"{AKN}heading")
    written = text_of(name) if name is not None else ""
    if written:
        return written
    num = node.find(f"{AKN}num")
    return text_of(num) if num is not None else ""


def render(act: dict, root: ET.Element) -> tuple[dict[str, list[str]], dict]:
    body = root.find(f".//{AKN}body")
    if body is None:
        raise ParseError("no <body> element")

    title = act["title"]
    out: list[tuple[str, list[str], str]] = []

    # Fedlex titles usually carry the adoption date already ("Federal Act of
    # 9 October 1992 on …"), so only add it when the title left it out.
    adopted = spoken_date(act["adopted"])
    dated = title if adopted in title else f"{title} of {adopted}"
    out.append(chip(
        f":INFO [align:center, links:{act['eli']}]",
        [slate_name(act), f"SR {act['sr']} — {dated}."],
        "masthead",
    ))

    # The provenance chip: which text this is, and whether it is behind the law.
    english = spoken_date(act["consolidated_en"])
    current = spoken_date(act["consolidated_current"])
    provenance = [preface_disclaimer(root)]
    if act["stale"]:
        provenance.append(
            f"This English text is the consolidation of {english}. The binding "
            f"German, French and Italian texts have since been consolidated to "
            f"{current}, so this translation is behind current law."
        )
    else:
        provenance.append(
            f"English consolidation of {english}, which is also the date of the "
            f"current binding text."
        )
    # Connected to the masthead: the title and what it is worth are one statement.
    out.append(chip(f":NOTE [links:{act['eli']}]", provenance, "masthead"))

    # The enacting formula and the constitutional basis are part of the act.
    preamble = root.find(f".//{AKN}preamble")
    if preamble is not None:
        spoken = [text_of(p) for p in preamble.findall(f"{AKN}p")]
        spoken = [s for s in spoken if s]
        if spoken:
            out.append(chip(":INFO", ["Preamble", *spoken], "preamble"))
            notes = [written for _, written in notes_in(preamble)]
            if notes:
                out.append(chip(":NOTE", notes, "preamble"))

    counts = {"articles": 0, "divisions": 0, "footnotes": 0, "repealed": 0, "empty": 0, "loose": 0}
    empty: list[str] = []   # articles the source leaves with a heading and no text

    def division_notes(node: ET.Element) -> list[str]:
        """A division's own footnote — anything deeper belongs to an article."""
        own = [
            text_of(note)
            for element in (
                node.find(f"{AKN}num"),
                node.find(f"{AKN}heading"),
                node.find(f"{AKN}subheading"),
            )
            if element is not None
            for note in element.findall(f".//{AKN}authorialNote")
        ]
        return [n for n in own if n]

    # Chips are connected into runs, and a divider is where a run ends: everything
    # under one Chapter or Section reads as a single connected block.
    section = {"n": 0, "id": "body"}

    def open_section() -> str:
        section["n"] += 1
        section["id"] = f"section-{section['n']}"
        return section["id"]

    def emit(node: ET.Element, out: list) -> None:
        # Text the node carries directly, before any of its sub-structure.
        direct = own_text(node)
        if direct:
            out.append(chip(":INFO", direct, section["id"]))
            counts["loose"] += 1
            # Loose text has footnotes too, and only articles and labelled
            # divisions were having theirs collected.
            adrift = [
                f"{anchor} — {written}" if anchor else written
                for element in node
                if element.tag[len(AKN):] in CONTENT
                for anchor, written in notes_in(element)
            ]
            if adrift:
                out.append(chip(":NOTE", adrift, section["id"]))
                counts["footnotes"] += len(adrift)

        for child in node:
            tag = child.tag[len(AKN):]
            if tag in LABELS or tag in CONTENT:
                continue  # collected by own_text() above
            if tag != "article":
                # Anything that is not an article is a container, whether or not
                # this parser has heard of it. Fedlex nests articles under <level>,
                # <proviso> and <transitional> as well as the named divisions, and
                # an emitter that only recursed into a fixed list dropped 1,674 of
                # the Code of Obligations' 1,686 articles without a word.
                label = labelled(child)
                if label:
                    # A divider, not a card: :PATH renders a heading on its own.
                    here = open_section()
                    subtitle = subheading_of(child)
                    out.append(chip(":PATH", [label, subtitle] if subtitle else [label], here))
                    counts["divisions"] += 1
                    own = division_notes(child)
                    if own:
                        out.append(chip(":NOTE", own, here))
                        counts["footnotes"] += len(own)

                emit(child, out)
            else:
                label = labelled(child)
                found = notes_in(child)
                lines = paragraph_lines(child)
                subtitle = subheading_of(child)
                if subtitle:
                    lines = [subtitle, *lines]
                notes = [
                    f"{anchor} — {written}" if anchor else written
                    for anchor, written in found
                ]

                if not lines:
                    # The heading above already names the article, so its own
                    # repeal note does not need to repeat the reference.
                    notes = [written for _, written in found]
                    # A repealed article is an empty shell whose footnote is the
                    # only thing left of it — Art. 64 is "Repealed by Annex I …".
                    # Dropping the note would leave a heading with no text at all.
                    if notes:
                        lines, notes = notes, []
                        counts["repealed"] += 1
                    else:
                        # Genuinely empty in the source: SR 443.14 Art. 4 carries a
                        # heading and nothing else. The article exists in the act,
                        # so it is emitted as its heading and counted, rather than
                        # invented, hidden, or allowed to fail the whole ordinance.
                        counts["empty"] += 1
                        empty.append(label or child.get("eId", "?"))

                out.append(chip(":INFO", [label, *lines], section["id"]))
                counts["articles"] += 1
                if notes:
                    # Amendment history — which is why these cannot simply be
                    # dropped. :NOTE is the chip for an annotation on the text.
                    out.append(chip(":NOTE", notes, section["id"]))
                    counts["footnotes"] += len(notes)

    fedlex = chip(
        ":LINK",
        [
            act["formats"]["html"],
            f"Official text on Fedlex — consolidation of {spoken_date(act['consolidated_en'])}",
        ],
        "source",
    )

    # One page per top-level division. An act is far easier to read as its own
    # Titles than as one scroll of a thousand lines, and the division is the unit
    # the drafters themselves chose. Acts with no divisions stay on home.
    # Any labelled container at body level earns a page, not only the named
    # divisions: the Code of Obligations keeps 83 articles in <transitional> and
    # <proviso> blocks — its transitional and final provisions — and a page list
    # built from DIVISIONS alone dropped every one of them.
    divisions = [
        c for c in body
        if c.tag[len(AKN):] not in CONTENT + LABELS
        and c.tag[len(AKN):] != "article"
        and labelled(c)
    ]
    paged = {id(c) for c in divisions}
    loose = [c for c in body if id(c) not in paged]
    pages: dict[str, list[str]] = {}

    banner = [
        f"// SR {act['sr']} — generated by src/parse.py from",
        f"// raw/acts/{act['sr']}/{act['consolidated_en']}/en.xml. Do not edit by hand.",
        "",
    ]

    if not divisions:
        open_section()
        emit(body, out)
        out.append(fedlex)
        return {"home": banner + lay_out(out)}, counts

    width = len(str(len(divisions)))
    contents: list[str] = []
    for index, division in enumerate(divisions, 1):
        slug = f"{index:0{width}d}-{kebab(heading_only(division))}"
        page: list[tuple[str, list[str], str]] = []

        # No chip repeating the division's own name: the page is named after it.
        # Its subtitle is different — that is text, not a restatement of the name.
        subtitle = subheading_of(division)
        if subtitle:
            page.append(chip(":PATH", [subtitle], "head"))
        own = division_notes(division)
        if own:
            page.append(chip(":NOTE", own, "head"))
            counts["footnotes"] += len(own)
        # Each page repeats the disclaimer: a reader can land on any page of the
        # act, and no page of a non-binding translation should omit that it is one.
        page.append(chip(":NOTE", [provenance[0]], "head"))

        before = counts["articles"]
        open_section()
        emit(division, page)
        page.append(fedlex)

        pages[slug] = [
            f"// SR {act['sr']} — {labelled(division)}",
            "// generated by src/parse.py. Do not edit by hand.",
            "",
        ] + lay_out(page)
        counts["divisions"] += 1
        found_here = counts["articles"] - before
        contents.append(
            f"{index}. {heading_only(division)} — "
            f"{found_here} article{'' if found_here == 1 else 's'}"
        )

    # Articles sitting at body level, outside every division, stay on home.
    if loose:
        # Whatever sits at body level outside a paged division stays on home, in
        # document order, so nothing can fall between the pages.
        open_section()
        holder = ET.Element(f"{AKN}wrapper")
        holder.extend(loose)
        emit(holder, out)

    out.append(chip(":INFO", ["Contents", *contents], "contents"))
    out.append(fedlex)
    pages["home"] = banner + lay_out(out)
    return pages, counts


def dot_slate(act: dict) -> str:
    description = (
        f"SR {act['sr']} — {act['title']}. Unofficial English mirror of the Fedlex "
        f"consolidation of {spoken_date(act['consolidated_en'])}. No legal force."
    )

    # Keys are the attribute names the slate page shows; @context aliases each one
    # onto its standard property so the JSON-LD still resolves as schema.org / ELI.
    schema = {
        "@context": {
            "@vocab": "https://schema.org/",
            "eli": "http://data.europa.eu/eli/ontology#",
            "documentType": "legislationType",
            "jurisdiction": "legislationJurisdiction",
            "reference": "legislationIdentifier",
            "dateInForce": "eli:first_date_entry_in_force",
            "adopted": "legislationDate",
            "consolidated": "legislationDateVersion",
        },
        "@type": "Legislation",
        "documentType": act["document_type"],
        "jurisdiction": "Switzerland",
        "reference": f"SR {act['sr']}",
        "dateInForce": act["in_force"],
        "adopted": act["adopted"],
        "consolidated": act["consolidated_en"],
        "legislationLegalForce": "https://schema.org/InForce",
        "inLanguage": "en",
        "isBasedOn": act["eli"],
    }
    line = json.dumps(schema, ensure_ascii=False, separators=(", ", ": "))
    if len(line) > 2000:
        raise ParseError(f"schema is {len(line)} chars; chext allows 2000")

    return "".join(
        f"{key}: {value}\n"
        for key, value in (
            ("name", slate_name(act)),
            ("description", description[:2000]),
            ("type", "LEGAL"),
            ("visibility", "PUBLIC"),
            # Named unconditionally. A thumbnail that is not in assets/ is ignored
            # by chext, so the key can be written before thumbnail.py has run and
            # the stages stay in any order.
            ("thumbnail", "cover.png"),
            ("schema", line),
        )
    )


def build(sr: str, census: dict) -> dict:
    act = next((a for a in census["acts"] if a["sr"] == sr), None)
    if act is None:
        raise ParseError(f"SR {sr} is not in the census")
    for field in ("document_type", "adopted", "in_force"):
        if not act[field]:
            raise ParseError(f"SR {sr} has no {field}; the attribute table needs it")

    source = ACTS_DIR / sr / act["consolidated_en"] / "en.xml"
    if not source.exists():
        raise ParseError(f"{source.relative_to(ROOT)} missing — run src/fetch.py {sr}")

    pages, counts = render(act, ET.parse(source).getroot())

    folder = slate_path(sr)
    page_dir = folder / "pages"
    page_dir.mkdir(parents=True, exist_ok=True)

    # A consolidation can rename or remove a division, so pages that are no longer
    # emitted have to go — otherwise the slate keeps serving a repealed Title.
    for stale in page_dir.glob("*.chext"):
        if stale.stem not in pages:
            stale.unlink()

    (folder / ".slate").write_text(dot_slate(act), encoding="utf-8")
    for slug, lines in pages.items():
        text = "\n".join(lines).rstrip() + "\n"
        (page_dir / f"{slug}.chext").write_text(text, encoding="utf-8")

    return {"sr": sr, "path": folder.relative_to(ROOT), "pages": len(pages), **counts}


def verify(sr: str, act: dict, result: dict) -> list[str]:
    """Compare the emitted slate against the source document.

    Structural loss here is silent by nature: an emitter that skips a container it
    does not recognise produces a perfectly valid slate that is simply missing 83
    articles. Counting the articles in the XML and in the output is what catches
    that, so it runs over the whole corpus rather than over the two acts that
    happened to be checked by hand.
    """
    root = ET.parse(ACTS_DIR / sr / act["consolidated_en"] / "en.xml").getroot()
    body = root.find(f".//{AKN}body")
    complaints = []

    expected = len(list(body.iter(f"{AKN}article")))
    if expected != result["articles"]:
        complaints.append(f"{expected} articles in source, {result['articles']} emitted")

    # Every word of the source has to appear somewhere in the pages. Reading the
    # document with itertext() would not do: joining its pieces with spaces splits
    # "Art. 305bis" into two words, and joining them without spaces welds
    # "…Provisions" onto "Art. 1". Inline elements close up, blocks take a space.
    def flat(element: ET.Element) -> str:
        parts = [element.text or ""]
        for child in element:
            inner = flat(child)
            parts.append(inner if child.tag[len(AKN):] in INLINE else f" {inner} ")
            parts.append(child.tail or "")
        return "".join(parts)

    joined: list[str] = []
    for element in (root.find(f".//{AKN}preamble"), body):
        if element is not None:
            joined.extend(re.findall(r"[\w']+", flat(element).lower()))

    # Comment lines are kept: a paged division's own label ("Title 1 Subject-
    # Matter") is carried by the page name rather than by a chip, and the header
    # comment is where it appears in the file.
    pages = (slate_path(sr) / "pages").glob("*.chext")
    text = re.sub(
        r"^(:[A-Z]+.*|\|)$", "", "\n".join(p.read_text(encoding="utf-8") for p in pages),
        flags=re.M,
    )
    emitted = collections.Counter(re.findall(r"[\w']+", text.lower()))
    missing = sum(
        count - emitted[token]
        for token, count in collections.Counter(joined).items()
        if count > emitted[token]
    )
    if missing > len(joined) * 0.001:
        complaints.append(f"{missing} of {len(joined)} source words missing from pages")
    return complaints


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sr", nargs="*", help="SR numbers to emit; default every act already fetched"
    )
    parser.add_argument("--quiet", action="store_true", help="only totals and failures")
    parser.add_argument("--verify", action="store_true", help="check output against source")
    args = parser.parse_args()

    census = json.loads(CENSUS_PATH.read_text(encoding="utf-8"))
    by_sr = {a["sr"]: a for a in census["acts"]}
    wanted = args.sr or [
        a["sr"]
        for a in census["acts"]
        if (ACTS_DIR / a["sr"] / a["consolidated_en"] / "en.xml").exists()
    ]

    totals = collections.Counter()
    failures: list[tuple[str, str]] = []
    for sr in wanted:
        try:
            result = build(sr, census)
        except (ParseError, NotImplementedError, ET.ParseError) as exc:
            # Collect and carry on: one odd document should not hide what the
            # other 255 would have told us about the parser.
            print(f"  FAIL {sr}: {exc}", file=sys.stderr)
            failures.append((sr, str(exc)))
            continue
        for key in ("pages", "articles", "divisions", "footnotes", "repealed", "empty", "loose"):
            totals[key] += result[key]
        totals["acts"] += 1
        if args.verify:
            for complaint in verify(sr, by_sr[sr], result):
                print(f"  LOST {sr}: {complaint}", file=sys.stderr)
                failures.append((sr, complaint))
        if not args.quiet:
            print(
                f"  {result['sr']:<14} {result['pages']:>2} pages, "
                f"{result['articles']:>4} articles, {result['divisions']:>3} divisions, "
                f"{result['footnotes']:>4} footnotes, {result['repealed']:>2} repealed"
                f"  → {result['path']}"
            )

    print(
        f"\n{totals['acts']} acts, {totals['pages']} pages, {totals['articles']} articles, "
        f"{totals['divisions']} divisions, {totals['footnotes']} footnotes, "
        f"{totals['repealed']} repealed, {totals['empty']} empty in source"
    )
    if failures:
        print(f"\n{len(failures)} failed:")
        for sr, why in failures:
            print(f"  {sr:<14} {why}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
