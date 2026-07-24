"""Topics — a non-taxonomic classification branch beside `biota/`.

A "topic" gives a home in the master to pictures that carry no species: a
landscape / context photo, and (later) other declared subjects such as planes.
The master's tree, `TargetPath`, `species.csv` and the DB `species` table are
generic chain-walkers (`specieslib.getPathToSpecies` walks the `Up` chain to a
root, joining `ScientificName`s). Taxonomy is just ONE chain rooted at `biota`;
a topic is a PARALLEL chain rooted at `topics`:

    topics / <topic> / <leaf>          e.g.  topics / landscape / 2026

So a topic needs no new tree machinery — only a synthetic chain injected into
the same `sc_dict` / `species_dict`. Once injected, `getPathToSpecies` yields
`topics/landscape/2026/` through the identical walk that produces
`biota/animalia/.../pica pica/`.

This module is the DECLARATIVE registry (topic keyword -> chain shape) plus the
resolver + injector. It is pure logic and writes nothing; the ingest side
(sv6Freeze, MasterDB) calls `inject_chain` / `route` in a later phase.

Design decisions locked 2026-07-24 (see topics_landscape_plan.md):
- Topic rows REUSE species.csv + the DB species table under a `topics` root;
  the ripple is contained behind `is_topic_node()` (ancestor == TOPICS_ROOT).
- The engine is generic; the registry is seeded with the single `landscape`
  row. Adding a topic later is a registry edit + a test, not code.
"""

import csv
import io
import logging
from dataclasses import dataclass, field

from engine.speciesclassification import SpeciesClassification, Level

logger = logging.getLogger("speciesviewer")

# Root path segment / dict key for the whole topics branch (mirrors "biota").
TOPICS_ROOT = "topics"


class TopicCollisionError(Exception):
    """A leaf key already exists in the classification under a different parent.

    Leaf keys double as on-disk path segments AND as globally-unique dict keys
    (the same constraint biota relies on — scientific names are unique). With a
    single year-based topic this never fires; it is the guardrail that makes the
    day a SECOND topic needs a namespaced leaf rule a loud failure, not a silent
    mis-file. See feedback_qa_as_guardrails.
    """


@dataclass(frozen=True)
class TopicRule:
    """One row of the declarative registry.

    key         canonical topic name; the middle path segment (topics/<key>/...)
    keywords    subject markers that select this topic (case-insensitive)
    display_nl  Dutch display name for the topic node
    display_en  English display name for the topic node
    leaf_rule   how the leaf segment is derived: "year" | "token"
    on_website  whether topic pages are published (False = master/backup only)
    """
    key: str
    keywords: frozenset
    display_nl: str
    display_en: str
    leaf_rule: str
    on_website: bool = False


@dataclass(frozen=True)
class LeafInfo:
    """A resolved leaf node: its key/path segment plus display names."""
    key: str
    display_nl: str
    display_en: str


# Seed registry. Mirrors the historic hardcoded LANDSCAPE_KEYWORDS, now as data.
DEFAULT_TOPICS = [
    TopicRule(
        key="landscape",
        keywords=frozenset({"landscape", "landschap"}),
        display_nl="landschap",
        display_en="landscape",
        leaf_rule="year",
        on_website=False,
    ),
]


class TopicRegistry:
    """The set of known topics. Default-seeded; optionally loaded from a CSV in
    the master metadata so adding a topic is data, not code."""

    def __init__(self, rules):
        self.rules = list(rules)
        self._by_keyword = {}
        for rule in self.rules:
            for kw in rule.keywords:
                self._by_keyword[kw.lower()] = rule

    @classmethod
    def default(cls):
        return cls(DEFAULT_TOPICS)

    @classmethod
    def load(cls, path):
        """Load from a `;`-delimited CSV
            key;keywords;display_nl;display_en;leaf_rule;on_website
        where `keywords` is comma-separated. Falls back to the default seed when
        the file is missing or empty — the engine is never left topic-less."""
        try:
            rules = []
            with io.open(path, mode="r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f, delimiter=";")
                for row in reader:
                    kws = frozenset(
                        k.strip().lower()
                        for k in (row.get("keywords") or "").split(",")
                        if k.strip()
                    )
                    if not row.get("key") or not kws:
                        continue
                    rules.append(TopicRule(
                        key=row["key"].strip().lower(),
                        keywords=kws,
                        display_nl=(row.get("display_nl") or "").strip(),
                        display_en=(row.get("display_en") or "").strip(),
                        leaf_rule=(row.get("leaf_rule") or "year").strip().lower(),
                        on_website=str(row.get("on_website", "")).strip().lower()
                        in ("yes", "true", "1"),
                    ))
            if rules:
                return cls(rules)
            logger.warning(f"Topic registry {path} empty — using default seed.")
        except FileNotFoundError:
            logger.info(f"No topic registry at {path} — using default seed.")
        except Exception as e:
            logger.warning(f"Topic registry {path} unreadable ({e}) — using default seed.")
        return cls.default()

    def all_keywords(self):
        """Every topic marker keyword — the generic replacement for the hardcoded
        SpeciesClassification.LANDSCAPE_KEYWORDS set."""
        return set(self._by_keyword.keys())

    def match(self, subject):
        """Return the TopicRule a subject selects, or None. A subject selects a
        topic when any of its tokens is a registered topic keyword. Reuses the
        classifier's tokenizer so a bare keyword and a list-repr
        ("['robin','landscape']") are handled identically."""
        for token in SpeciesClassification._subject_tokens(subject):
            rule = self._by_keyword.get(token)
            if rule is not None:
                return rule
        return None


def _year_from_picture(picture):
    """Best-effort 4-digit year from a picture's capture time. Returns "" when
    no year can be found (caller then declines to route — a topic photo with no
    date has no year bucket)."""
    for key in ("DateTimeOriginal", "XMP:DateTimeOriginal", "EXIF:DateTimeOriginal"):
        val = str(picture.get(key, "")) if hasattr(picture, "get") else ""
        val = val.strip()
        # capture times look like "2026:05:08 10:30:00" — take the leading run of digits
        if len(val) >= 4 and val[:4].isdigit():
            return val[:4]
    return ""


def resolve_leaf(rule, picture):
    """Apply a topic's leaf_rule to a picture -> LeafInfo, or None when the leaf
    cannot be determined (e.g. year rule but no capture date)."""
    if rule.leaf_rule == "year":
        year = _year_from_picture(picture)
        if not year:
            return None
        return LeafInfo(key=year, display_nl=year, display_en=year)
    if rule.leaf_rule == "token":
        # future topics (e.g. planes/F16): leaf = a second, non-keyword subject
        # token. Not exercised yet — landscape is year-based.
        for token in SpeciesClassification._subject_tokens(
                str(picture.get("subject", "")) if hasattr(picture, "get") else ""):
            if token not in rule.keywords:
                return LeafInfo(key=token, display_nl=token, display_en=token)
        return None
    logger.warning(f"Unknown leaf_rule '{rule.leaf_rule}' for topic '{rule.key}'.")
    return None


def inject_chain(sc, rule, leaf):
    """Inject the synthetic chain topics/<topic>/<leaf> into a
    SpeciesClassification's sc_dict (idempotent), returning the leaf key (which
    becomes the picture's WetSoortNaam). The species_dict tree is then built by
    the ordinary sp.getSpecies(leaf_key) path, exactly like a real species.

    Raises TopicCollisionError if the leaf key already lives under a different
    parent (see the class docstring)."""
    existing = sc.sc_dict.get(leaf.key)
    if existing is not None and existing.get("Up") not in (rule.key, ""):
        raise TopicCollisionError(
            f"Leaf '{leaf.key}' already classified under '{existing.get('Up')}', "
            f"cannot reuse it under topic '{rule.key}'. A leaf key must be "
            f"globally unique; give this topic a namespaced leaf rule.")

    # Root (Up="" -> the top of the tree, mirrors biota). addSpeciesLevel with an
    # empty upname skips the Leaves back-link, just as for biota.
    sc.addSpeciesLevel(Level.TOPICS, TOPICS_ROOT, "", "", "")
    sc.addSpeciesLevel(Level.TOPIC, rule.key, rule.display_nl, rule.display_en, TOPICS_ROOT)
    sc.addSpeciesLevel(Level.TOPICLEAF, leaf.key, leaf.display_nl, leaf.display_en, rule.key)
    return leaf.key


def route(sc, sp, registry, subject, picture):
    """One-call convenience for the ingest side (Phase 3): given a subject that
    resolved to NO species and a picture, return the leaf WetSoortNaam to file it
    under (topics/<topic>/<leaf>), or None if it is not a topic / has no leaf.

    The species-first precedence (a subject naming a real species is never a
    topic) is the CALLER's responsibility: call this only when species
    resolution already returned empty."""
    rule = registry.match(subject)
    if rule is None:
        return None
    leaf = resolve_leaf(rule, picture)
    if leaf is None:
        return None
    inject_chain(sc, rule, leaf)
    # Mirror into species_dict via the ordinary path so counters/best-JPG work.
    sp.getSpecies(leaf.key)
    return leaf.key


def is_topic_node(records, name):
    """True if `name` sits in the topics branch — i.e. walking its Up chain
    reaches TOPICS_ROOT. `records` is an sc_dict or species_dict (both key by
    ScientificName and carry 'Up'). This ancestor walk is the ONE containment
    predicate every species-keyed consumer (website pages, map, counters) uses
    to skip topics; it is robust to the leaf's Level."""
    if name == TOPICS_ROOT:
        return True
    seen = set()
    cur = name
    while cur and cur not in seen:
        seen.add(cur)
        rec = records.get(cur)
        if rec is None:
            return False
        up = rec.get("Up", "")
        if up == TOPICS_ROOT:
            return True
        cur = up
    return False
