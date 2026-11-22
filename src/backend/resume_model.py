"""
    Deterministic handling of the structured master CV and the model's tailoring selection.

    The model never writes resume content directly. It picks section, entry and bullet IDs from
    the parsed CV (and may reword a bullet). This module checks that selection against the CV and
    repairs it using only CV content:

      - an entry listed under the wrong section is moved to the section it came from, so an
        experience entry can never be printed under Projects (whatever the CV's layout);
      - unknown or duplicate IDs are dropped;
      - a reworded bullet that introduces a number the source bullet does not contain, or that
        grows far longer than its source, falls back to the CV's own wording;
      - Education is added back if the model left it out.

    Every repair is reported as a note. Nothing is regenerated (DESIGN.md §8.4).
"""

import re
import unicodedata
from typing import Union

from .schemas import (
    LIST_KINDS, Contact, CVBullet, CVEntry, CVSection, MasterCV, ParsedCV, TailoredResume,
)

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


# ---------- IDs ----------

def assign_ids(cv: Union[ParsedCV, MasterCV]) -> MasterCV:
    """
        Desc:
            Builds a MasterCV whose IDs are derived from position (s1, s1.e2, s1.e2.b3).
            Any IDs already present are discarded, so IDs sent by a client are never trusted.
        Args:
            cv (ParsedCV | MasterCV): Model parse output or a stored MasterCV.
        Returns:
            MasterCV: The same content with fresh IDs.
    """
    sections = []
    for si, section in enumerate(cv.sections, 1):
        sid = f"s{si}"
        entries = []
        for ei, entry in enumerate(section.entries, 1):
            eid = f"{sid}.e{ei}"
            texts = [b if isinstance(b, str) else b.text for b in entry.bullets]
            bullets = [CVBullet(id=f"{eid}.b{bi}", text=t.strip())
                       for bi, t in enumerate(texts, 1) if t and t.strip()]
            entries.append(CVEntry(
                id=eid, title=entry.title.strip(), organization=_clean(entry.organization),
                dates=_clean(entry.dates), location=_clean(entry.location), tech=_clean(entry.tech),
                bullets=bullets,
            ))
        sections.append(CVSection(id=sid, heading=section.heading.strip(), kind=section.kind,
                                  entries=entries))
    contact = Contact(**cv.contact.model_dump())
    return MasterCV(contact=contact, sections=sections)


def _clean(value):
    value = (value or "").strip()
    return value or None


# ---------- parse fidelity ----------

def _alnum(text):
    text = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(ch for ch in text if ch.isalnum())


def unmatched_bullets(cv: MasterCV, resume_text: str) -> list[str]:
    """
        Desc:
            Bullets in the parse whose text does not appear in the CV text (ignoring case,
            punctuation and line breaks). The parse is supposed to copy text, not rewrite it,
            so these are flagged for the user to check.
        Returns:
            list[str]: "Heading › Entry: bullet" descriptions, in CV order.
    """
    source = _alnum(resume_text)
    misses = []
    for section in cv.sections:
        for entry in section.entries:
            for bullet in entry.bullets:
                needle = _alnum(bullet.text)
                if needle and needle not in source:
                    label = f"{section.heading} › {entry.title}" if entry.title else section.heading
                    misses.append(f"{label}: {bullet.text}")
    return misses


# ---------- prompt view ----------

def describe_for_prompt(cv: MasterCV) -> str:
    """
        Desc:
            Plain-text view of the CV with IDs, for the tailoring prompt. Contact details are left
            out because code copies them onto the resume.
    """
    lines = []
    for section in cv.sections:
        lines.append(f'SECTION {section.id} | heading: "{section.heading}" | kind: {section.kind}')
        for entry in section.entries:
            details = " | ".join(
                f"{label}: {value}"
                for label, value in (("organization", entry.organization), ("dates", entry.dates),
                                     ("location", entry.location), ("tech", entry.tech))
                if value
            )
            lines.append(f"  ENTRY {entry.id}: {entry.title or '(untitled)'}"
                         + (f" | {details}" if details else ""))
            for bullet in entry.bullets:
                lines.append(f"    {bullet.id}: {bullet.text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------- selection check ----------

def _numbers(text):
    return {n.replace(",", "") for n in _NUMBER_RE.findall(text or "")}


def check_rewrite(source: CVBullet, entry: CVEntry, rewrite: str):
    """
        Desc:
            Decides whether a reworded bullet may replace its source.
        Returns:
            str | None: None if the rewrite is acceptable, otherwise the reason it was rejected.
    """
    allowed = _numbers(source.text) | _numbers(entry.title) | _numbers(entry.tech)
    invented = sorted(_numbers(rewrite) - allowed)
    if invented:
        return f"it added numbers not in the CV ({', '.join(invented)})"
    if len(rewrite) > 2 * len(source.text) + 40:
        return "it was much longer than the original"
    return None


def check_selection(cv: MasterCV, tailored: TailoredResume):
    """
        Desc:
            Turns the model's ID selection into the sections to render, repairing it with CV
            content only.
        Args:
            cv (MasterCV): The CV the selection refers to (with IDs from assign_ids).
            tailored (TailoredResume): The model's selection.
        Returns:
            tuple[list[CVSection], list[str]]: Sections to render (in the model's order, holding
            only the chosen entries and bullets, with accepted rewrites applied), and notes
            describing every correction made.
    """
    sections = {s.id: s for s in cv.sections}
    entry_home = {}      # entry id -> (section id, CVEntry)
    bullet_home = {}     # bullet id -> (entry id, CVBullet)
    for section in cv.sections:
        for entry in section.entries:
            entry_home[entry.id] = (section.id, entry)
            for bullet in entry.bullets:
                bullet_home[bullet.id] = (entry.id, bullet)

    chosen = {}          # section id -> list[CVEntry], insertion order = output order
    seen_entries = set()
    notes = []

    for pick in tailored.sections:
        listed_under = sections.get(pick.section_id)
        if listed_under is None:
            notes.append(f"Ignored unknown section ID {pick.section_id!r}.")
        else:
            chosen.setdefault(listed_under.id, [])

        for entry_pick in pick.entries:
            home = entry_home.get(entry_pick.entry_id)
            if home is None:
                notes.append(f"Ignored unknown entry ID {entry_pick.entry_id!r}.")
                continue
            if entry_pick.entry_id in seen_entries:
                notes.append(f"Ignored a repeat of {_entry_label(home[1])}.")
                continue
            seen_entries.add(entry_pick.entry_id)
            home_id, entry = home
            if listed_under is not None and home_id != listed_under.id:
                notes.append(f"Moved {_entry_label(entry)} back to “{sections[home_id].heading}” "
                             f"(it was listed under “{listed_under.heading}”).")
            bullets = _pick_bullets(entry, entry_pick.bullets, bullet_home, notes)
            chosen.setdefault(home_id, []).append(entry.model_copy(update={"bullets": bullets}))

    for section in cv.sections:
        if section.kind == "education" and section.entries and not chosen.get(section.id):
            chosen[section.id] = [e.model_copy(deep=True) for e in section.entries]
            notes.append(f"Added “{section.heading}”, which the model left out.")

    result = []
    for section_id, entries in chosen.items():
        section = sections[section_id]
        if section.kind in LIST_KINDS:
            # A list line with every item dropped would print as a bare label.
            entries = [e for e in entries if e.bullets or not _source_entry(section, e.id).bullets]
        if entries:
            result.append(section.model_copy(update={"entries": entries}))
    return result, notes


def _source_entry(section, entry_id):
    return next(e for e in section.entries if e.id == entry_id)


def _entry_label(entry):
    return f"“{entry.title}”" if entry.title else "an untitled entry"


def _pick_bullets(entry, picks, bullet_home, notes):
    bullets = []
    seen = set()
    for pick in picks:
        home = bullet_home.get(pick.bullet_id)
        if home is None or home[0] != entry.id:
            notes.append(f"Ignored bullet ID {pick.bullet_id!r}, which is not part of "
                         f"{_entry_label(entry)}.")
            continue
        if pick.bullet_id in seen:
            continue
        seen.add(pick.bullet_id)
        source = home[1]
        text = source.text
        rewrite = (pick.rewrite or "").strip()
        if rewrite and rewrite != source.text:
            problem = check_rewrite(source, entry, rewrite)
            if problem:
                notes.append(f"Kept the CV's wording for a bullet in {_entry_label(entry)}: "
                             f"the rewrite was rejected because {problem}.")
            else:
                text = rewrite
        bullets.append(CVBullet(id=source.id, text=text))
    return bullets
