# ch-law

Switzerland Law as a slate collection.

An unofficial mirror of Swiss federal law (the *Systematic Compilation*, SR) from
[Fedlex](https://fedlex.data.admin.ch), converted to
[chext](https://slatesource.com/chips/chext) for Slatesource.

**The English translations published by Fedlex have no legal force.** Only the
German, French and Italian texts are binding. Nothing here is machine-translated:
an act appears in this repo only if Fedlex publishes an official English
consolidated version of it.

## Scope

Of 5,087 in-force SR entries, **256** have a usable official English consolidation.
Those 256 are the entire corpus of this repo.

Getting to that number takes three filters, and each one matters:

| | Acts |
|---|---|
| In force | 5,087 |
| …with an English *expression* | 3,274 |
| …where that expression has an English **XML document** | 258 |
| …that is actually downloadable and actually English | **256** |

The 3,274 is a trap: 2,410 of those are class-0 treaties whose English expression is
a metadata record with no document behind it. Of the 258 that survive, two more drop
out and are recorded in `data/skipped.json` — SR 935.121.42 advertises an English
XML with no download URL at all, and SR 520.31 serves an *Italian* document under
its `/en/` path. Acts with no English at all are not listed individually; they are
simply absent.

That yields **410 slates**: 256 acts plus the 154 category slates above them.

## File structure

This is the target layout. Built so far: `census.py`, `fedlex.py`, `fetch.py`,
`parse.py`, the census output, and SR 231.1 as the first emitted slate. `tree.py`
and the category slates above the acts are what remain.

```
src/                     pipeline stages, run in order
  census.py                SPARQL → data/census.json      (what exists in English)
  fetch.py                 census → raw/acts/…/en.xml     (the only networked stage)
  tree.py                  SR taxonomy → category slates  (the classification tree)
  parse.py                 raw XML → .slate + chext       (the acts themselves)
  thumbnail.py             SR number → assets/cover.png   (procedural, seeded)
  provenance.py            shared provenance-chip emitter

data/
  census.json              one record per act: SR number, EN title, abbreviation,
                           ELI URI, instrument type, adoption date, entry-into-force
                           date, English consolidation date, current-text date,
                           staleness flag, download URL per format
  tree-labels.en.json      hand-reviewed English labels for the 29 category nodes
                           that have no English label in the Fedlex vocabulary
  skipped.json             acts that have an English document but are still not
                           emitted, each with the reason

raw/                      everything fetched, committed verbatim
  sparql/census-<date>.json
  acts/<sr>/<consolidation-date>/en.xml

slates/                   emitted slates, mirroring the SR classification tree

tests/                    golden-file parser tests, run offline against raw/
                           — no network, so they assert byte-for-byte determinism
.github/workflows/nightly.yml
```

### Layout of `slates/`

`slates/` mirrors the SR numbering, one directory level per digit-group. **Every
node is a real slate** — categories included — so each directory carries its own
`.slate` and `pages/home.chext`:

```
slates/
  2/                              class 2 — Private law
    .slate
    pages/home.chext              overview of private law + links to its groups
    23/                           group 23 — Intellectual property
      .slate
      pages/home.chext            overview + links to its acts
      231.1/                      SR 231.1 — Copyright Act
        .slate
        pages/home.chext
```

A category slate is not a bare index. Its `home.chext` is a table of contents *and*
an overview: what this area of Swiss law covers, how it is subdivided, and how many
of its acts exist in English — a reader landing on `/2/23` should learn what Swiss
intellectual property law consists of, not just see a list of links.

Category names come from the Fedlex vocabulary's English `skos:prefLabel` where it
exists. For the 29 nodes that have no English label, the name comes from
`data/tree-labels.en.json`, which is hand-written, reviewed and committed — the
build fails rather than falling back to a German label or inventing one.

Children are linked with `:SLATE` chips carrying root-relative, extension-less
paths:

```
:SLATE
/2/23/231.1
```

Dangling paths are valid output — a category may link an act that has no English
version and therefore no slate of its own.

### Acts are slates; every act is paginated by its own divisions

Every act has the same shape, so there is no separate "folder form". **One page per
top-level division**, in document order: `home.chext` carries the title, the
provenance and a table of contents, and each Title / Book / Part / Division becomes
`pages/<n>-<kebab-cased-heading>.chext`.

```
slates/2/23/231.1/                SR 231.1 — Copyright Act (113 articles)
  .slate
  pages/home.chext                title, provenance, preamble, contents
  pages/1-subject-matter.chext
  pages/2-copyright.chext
  pages/3-related-rights.chext
  …
  pages/9-final-provisions.chext
```

There is no article-count threshold. The division is the unit the drafters chose,
and it is what makes an act readable: 113 articles in one page is 1,155 lines of
scroll, while the same act as its 9 Titles is navigable. An act with no divisions
at all keeps everything on `home.chext`.

The leading number is what fixes page order, since the file name — not the heading —
becomes the page's name and slug. It is zero-padded to the width of the division
count, so a 12-Book act sorts `01-` … `12-` rather than `1-, 10-, 11-, 2-`. Headings
are cut to 48 characters on a word boundary, and a cut that would land on a dangling
*of*, *the* or *which* backs up a word.

Every page repeats the disclaimer. A reader can land on `3-related-rights` without
ever seeing `home`, and no page of a non-binding translation should omit that it is
one.

Within a page, chips are **connected**: a bare `|` on the last line of a chip ties
it to the chip below, so a Chapter and all its articles render as one run rather
than as a pile of separate cards. The chain breaks exactly where the structure
does — at every `:PATH` divider, and around the page's own disclaimer and source
link. SR 231.1 carries 177 connectors across its ten pages, its longest run being
a 29-chip Chapter.

Structural headings below page level become `:PATH` chips carrying only their
label ("Chapter 2 Author"). The top-level division gets no chip of its own at all,
because the page is named after it.

The table of contents lists the divisions and their article counts as text.
`:SLATE` chips reference *other slates*, not sibling pages, so a page cannot yet
link to its neighbours — the platform's own page navigation carries that.

### Slate anatomy

Two files describe every slate, and they answer different questions.

`.slate` is what Slatesource itself reads — how the slate is listed, described and
published:

```
name: Copyright Act (CopA)
description: SR 231.1 — Federal Act on Copyright and Related Rights. Unofficial
  English mirror of the Fedlex consolidation of 1 July 2025. No legal force.
type: LEGAL
visibility: PUBLIC
```

`type: LEGAL` is a slate type introduced for exactly this kind of content —
statutes, regulations, terms of service, privacy policies — text that is read as
authority rather than as an article or a product page. Slatesource's own privacy
policy and terms move onto it too.

### Thumbnails are a fingerprint of the act

Every slate's `assets/cover.png` is generated, not drawn. The seed is the act's own
reference — `SR 231.1` — hashed into the RNG that decides the whole composition, so
the same act always produces the same image, no two acts produce the same one, and
nothing has to be stored except the seed.

The vocabulary is deliberately narrow, because 410 of these have to look like one
collection: paper ground, a single greyscale field (strata, rings, a modular grid,
or a hatch) confined to one block on a margin grid, and exactly one Swiss-red mark
drawn last, on top. The empty paper around the block is what keeps it legible at
thumbnail size.

```
python src/thumbnail.py 231.1            # into the slate's assets/
python src/thumbnail.py --seed "231.1 Art. 13a" --out /tmp/art.png
```

The seed is a free string, so per-article images are the same call with a longer
seed. `.slate` names `thumbnail: cover.png` unconditionally — chext ignores a
thumbnail that is not in `assets/`, so the stages can run in any order.

This is the one stage that needs a dependency (Pillow); census, fetch and parse are
stdlib-only.

Output is a 512-pixel PNG quantised to 64 colours — about **10 KB per slate**, or
some 4.5 MB across the full corpus. That combination was measured rather than
assumed, and JPEG lost badly:

| | 1024px | 512px |
|---|---|---|
| PNG, 64 colours | 32.8 KB | **10.0 KB** |
| JPEG q85 | 73.3 KB | 33.2 KB |
| JPEG q75 | 56.7 KB | 25.7 KB |
| WEBP | 36.8 KB | 20.4 KB |

Flat fills, hard edges and film grain are precisely what JPEG is worst at, so it
comes out two to five times heavier *and* rings around the red. Quantising to 16
colours is heavier than 64, because forcing the dither pattern costs more than the
palette saves. The grain is the single most expensive thing in the file — noise is
what a palette PNG cannot compress — and `--no-grain` drops 10 KB to 3.5 KB for
anyone who wants the composition flat.

### `schema:` — the attribute table

`.slate` takes an optional `schema:` key holding a JSON-LD object. Slatesource
publishes it as `application/ld+json` and renders its properties as the slate's
attribute table. A developer tool would carry

```
schema: {"@type": "SoftwareApplication", "applicationCategory": "DeveloperApplication"}
```

— which renders as **Application Category / DeveloperApplication**. Every act slate
in this repo carries a `Legislation` object instead. Formatted for reading:

```
{"@context": {"@vocab": "https://schema.org/",
              "eli": "http://data.europa.eu/eli/ontology#",
              "documentType": "legislationType",
              "jurisdiction": "legislationJurisdiction",
              "reference": "legislationIdentifier",
              "dateInForce": "eli:first_date_entry_in_force",
              "adopted": "legislationDate",
              "consolidated": "legislationDateVersion"},
 "@type": "Legislation",
 "documentType": "Federal act",
 "jurisdiction": "Switzerland",
 "reference": "SR 231.1",
 "dateInForce": "1993-07-01",
 "adopted": "1992-10-09",
 "consolidated": "2025-07-01",
 "legislationLegalForce": "https://schema.org/InForce",
 "inLanguage": "en",
 "isBasedOn": "https://fedlex.data.admin.ch/eli/cc/1993/1798_1798_1798"}
```

**`schema:` is one line.** chext reads it as JSON on a single line of at most 2,000
characters, and anything it cannot parse is dropped silently rather than reported —
so a pretty-printed block like the one above would cost the slate its entire
attribute table with no error anywhere. `parse.py` emits it as one line (671
characters for this act) and fails the build if it ever exceeds the limit. See
`slates/2/23/231.1/.slate` for the real thing.

Document Type is the vocabulary's own English label, taken verbatim: Fedlex writes
*Federal act*, not *Federal Act*, and correcting its casing would be inventing.

which renders as:

| | |
|---|---|
| **Document Type** | Federal Act |
| **Jurisdiction** | Switzerland |
| **Reference** | SR 231.1 |
| **Date In Force** | 1993-07-01 |
| **Adopted** | 1992-10-09 |
| **Consolidated** | 2025-07-01 |

The first four are mandatory on every act slate — the same four an
England-and-Wales act would show as *Act of Parliament / England and Wales /
2024 c. 12*. An act whose census record is missing any of them fails the build
rather than emitting a partial attribute table, the same rule as the category
labels.

| Attribute | Source | Copyright Act |
|---|---|---|
| Document Type | `jolux:typeDocument`, English `skos:prefLabel` | Federal act |
| Jurisdiction | constant for this corpus | Switzerland |
| Reference | SR number | SR 231.1 |
| Date In Force | `jolux:dateEntryInForce` | 1993-07-01 |

The keys are the attribute names, so what a reader sees and what a parser reads are
the same string — no lookup table between the page and the JSON. The `@context`
aliases each one onto its standard property, so `dateInForce` **is**
`eli:first_date_entry_in_force` and `reference` **is** schema.org's
`legislationIdentifier` after expansion: the table stays readable and the JSON-LD
still resolves for anyone consuming it as ELI or as schema.org. That aliasing is
also why the block needs an explicit `@context` at all — the `SoftwareApplication`
example above uses schema.org's own names throughout and should carry none.

Document Type is the one that varies. The 256 acts use 11 of the vocabulary's
types — 121 *Federal act*, 106 *Federal Council ordinance*, then a long tail down to
one *Emergency federal act* and one *Federal decree subject to mandatory
referendum*. Unlike the classification tree, this vocabulary is fully translated:
every type in the corpus has an English label, so no hand-written fallback file is
needed.

The three dates are separate on purpose, and the difference is the whole "English
lags the binding text" problem in three fields:

- **Adopted** — adoption by the Federal Assembly.
- **Date In Force** — entry into force.
- **Consolidated** — the consolidation this text reflects. It is the **English**
  consolidation date, the same value as the `[consolidated:]` chip, so for the 16
  stale acts it is deliberately older than the current binding text. The provenance
  chip is where both dates appear side by side; the schema block only ever states
  the date of the text actually published here.

`legislationLegalForce` is `InForce` throughout — repealed acts are outside the
corpus — and `isBasedOn` points at the ELI URI of the binding original, which is
the machine-readable form of the disclaimer. Neither is aliased; neither is meant
for the attribute table.

Category slates are not legislation and do not pretend to be. They carry

```
schema: {"@type": "Collection", "collectionSize": 12}
```

where `collectionSize` is the number of acts below that node that exist in English —
the same count the overview text states in prose.

`pages/home.chext` opens with the legal metadata block, which stays as specified:

```
[slug:231.1] [lang:en] [type:law] [status:in-force]
[source:https://fedlex.data.admin.ch/eli/cc/1993/1798_1798_1798]
[consolidated:2025-07-01] [disclaimer:non-binding-translation]
```

The overlap between the two `type` keys is deliberate: `.slate`'s `LEGAL` says what
kind of *slate* this is, while `[type:law]` says what kind of *instrument* the text
is — an act, an ordinance, a treaty — and that distinction survives into the data.

Every article chip carries a stable `[id:]` anchor for future deep links, derived
from the Akoma Ntoso `eId`, so `art_39_a` ("Art. 39a") becomes `[id:art-39a]`. In
v1, inline citations within article text stay plain text; only structural
relationships (parent, children, related acts) are chips.

## Three things the data does that surprise people

**English lags the binding text.** For 16 of the 256 acts, the newest English
consolidation is older than the current German/French text — SR 814.501 by more than
four years. Those acts are still published here, with the provenance chip naming
both dates and saying plainly that the English is behind current law. Picking "the
latest consolidation, then look for English" is wrong and silently loses 16 acts,
SR 311.0 (the Criminal Code) among them: its two newest consolidations are DE/FR/IT
only.

**Download URLs are not guessable.** The ELI URI serves a web page, not the
document. The real file comes from `jolux:isExemplifiedBy` in the RDF graph, and it
carries unpredictable suffixes (`…-en-xml.xml`, but `…-en-xml-7.xml` for SR 101).

**The endpoint fails soft.** Under load it answers HTTP 200 with an HTML error page
instead of SPARQL JSON, so every query validates the response shape before parsing.
Server-side aggregation is also unreliable here — a `GROUP BY` + `MAX()` returned
dates that per-act queries contradicted — so the pipeline pulls raw rows and
aggregates in Python, keyed on the act's ELI URI rather than its SR number.

## Determinism

Same input, same bytes. The census takes `--as-of YYYY-MM-DD` because the SPARQL
query filters on "today"; re-running any stage with the same inputs must reproduce
its output exactly. The parser runs against committed `raw/` files with no network
access. Article text keeps its typographic characters verbatim — curly quotes,
dashes, ordinals — with one exception: U+00A0 is collapsed to a normal space.
Fedlex applies non-breaking spaces inconsistently, 12 of SR 231.1's 113 article
numbers carrying one and the rest not, so reproducing them faithfully would
preserve a typesetting accident and render *Art. 40* differently from *Art. 41*.

## Licence

Swiss federal law is not subject to copyright (URG/CopA Art. 5). The pipeline code
in `src/` is covered by this repository's licence.
