"""No article may be shorter on Slatesource than it is in the Fedlex XML.

Two things have to hold for that, and each is checked over the whole corpus:

  * every chip fits Slatesource's 1,000-character store, which otherwise cuts the
    text without a word — Art. 24 FADP was served ending "…guaranteed by maki";
  * every paragraph of every article in raw/ appears in full in the act's chext.

Offline: reads only raw/, data/census.json and slates/.

    python -m unittest discover tests
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import parse  # noqa: E402

MARKER = re.compile(r":[A-Z]+(\.half)?( \[.*\])?")


def acts():
    census = json.loads(parse.CENSUS_PATH.read_text(encoding="utf-8"))
    for act in census["acts"]:
        source = parse.ACTS_DIR / act["sr"] / act["consolidated_en"] / "en.xml"
        if source.exists():
            yield act, source, sorted((parse.slate_path(act["sr"]) / "pages").glob("*.chext"))


def chips(page: pathlib.Path) -> list[list[str]]:
    """The body lines of each chip on a page, without marker or connector."""
    found: list[list[str]] = []
    for line in page.read_text(encoding="utf-8").split("\n"):
        if MARKER.fullmatch(line):
            found.append([])
        elif found and line != "|":
            found[-1].append(line)
    for body in found:
        while body and not body[-1]:
            body.pop()
    return found


def squeeze(text: str) -> str:
    """Letters and digits only, so line and chip breaks do not count as loss."""
    return re.sub(r"[\W_]+", "", text.lower())


class ChipText(unittest.TestCase):
    def test_corpus_is_present(self):
        self.assertGreater(sum(1 for _ in acts()), 200)

    def test_no_chip_exceeds_the_store(self):
        over = [
            f"{page.relative_to(ROOT)}: {body[0][:50]!r} is {parse.size(body)} bytes"
            for _, _, pages in acts()
            for page in pages
            for body in chips(page)
            if parse.size(body) > parse.CHIP_LIMIT
        ]
        self.assertEqual(over, [], f"{len(over)} chips would be truncated")

    def test_every_article_is_whole(self):
        short = []
        for act, source, pages in acts():
            # Continuation headings are the one thing the emitter adds to the text.
            emitted = squeeze("\n".join(
                line
                for page in pages
                for body in chips(page)
                for i, line in enumerate(body)
                if not (i == 0 and line.endswith(" (continued)"))
            ))
            body = ET.parse(source).getroot().find(f".//{parse.AKN}body")
            for article in body.iter(f"{parse.AKN}article"):
                for paragraph in article.iter(f"{parse.AKN}p"):
                    wanted = parse.text_of(paragraph)
                    if squeeze(wanted) not in emitted:
                        short.append(f"SR {act['sr']} {article.get('eId')}: {wanted[:60]!r}")
        self.assertEqual(short, [], f"{len(short)} paragraphs cut or missing")


class Split(unittest.TestCase):
    def test_short_block_is_left_alone(self):
        self.assertEqual(parse.split(":INFO", ["Art. 1", "1 Text."], "Art. 1 (continued)"),
                         [["Art. 1", "1 Text."]])

    def test_long_article_keeps_every_line_in_order(self):
        lines = ["Art. 9 Heading"] + [f"{n} " + "word " * 60 for n in range(1, 12)]
        lines = [line.strip() for line in lines]
        parts = parse.split(":INFO", lines, "Art. 9 (continued)")
        self.assertGreater(len(parts), 1)
        self.assertEqual(parts[0][0], "Art. 9 Heading")
        self.assertTrue(all(p[0] == "Art. 9 (continued)" for p in parts[1:]))
        self.assertEqual([l for p in parts for l in p[1:]], lines[1:])
        self.assertTrue(all(parse.size(p) <= parse.CHIP_LIMIT for p in parts))

    def test_single_long_line_breaks_between_words(self):
        line = "alpha beta gamma. " * 200
        parts = parse.split(":NOTE", [line.strip()], None)
        self.assertTrue(all(parse.size(p) <= parse.CHIP_LIMIT for p in parts))
        self.assertEqual(" ".join(l for p in parts for l in p), line.strip())


class Boundaries(unittest.TestCase):
    """Words either side of a block boundary must not be welded together."""

    @staticmethod
    def element(xml: str) -> ET.Element:
        return ET.fromstring(f'<x xmlns="{parse.AKN[1:-1]}">{xml}</x>')[0]

    def test_line_break_separates_words(self):
        p = self.element("<p>dated 29 October 2002<br/>and the opinion<br/>, as amended</p>")
        self.assertEqual(parse.text_of(p), "dated 29 October 2002 and the opinion, as amended")

    def test_inline_markup_closes_up(self):
        p = self.element("<p>Art. 305<sup>bis</sup> applies</p>")
        self.assertEqual(parse.text_of(p), "Art. 305bis applies")

    def test_table_is_one_line_per_row(self):
        table = self.element(
            "<table><tr><th/><th><p>Swiss francs</p></th></tr>"
            "<tr><td><p>Certificates, reports</p></td><td><p>200–2 000</p></td></tr></table>"
        )
        self.assertEqual(parse.lines_of(table),
                         ["| Swiss francs", "Certificates, reports | 200–2 000"])


if __name__ == "__main__":
    unittest.main()
