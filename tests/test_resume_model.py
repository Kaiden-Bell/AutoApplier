"""Selection checks: the model picks IDs, code keeps every entry in its own section."""

import pytest

from backend import resume_model
from backend.schemas import (
    BulletPick, CVBullet, CVEntry, EntryPick, ParsedContact, ParsedCV, SectionPick, TailoredResume,
)
from cv_fixtures import entry, experience_only_cv, projects_and_experience_cv, section


def pick(*sections):
    """pick(("s3", [("s3.e1", ["s3.e1.b1", ("s3.e1.b2", "reworded")])]))"""
    out = []
    for section_id, entries in sections:
        eps = []
        for entry_id, bullets in entries:
            bps = [BulletPick(bullet_id=b, rewrite=None) if isinstance(b, str)
                   else BulletPick(bullet_id=b[0], rewrite=b[1]) for b in bullets]
            eps.append(EntryPick(entry_id=entry_id, bullets=bps))
        out.append(SectionPick(section_id=section_id, entries=eps))
    return TailoredResume(is_qualified=True, gap_analysis="", sections=out)


def by_heading(sections):
    return {s.heading: [e.title for e in s.entries] for s in sections}


EDU = ("s5", [("s5.e1", [])])


def test_ids_are_positional_and_client_ids_are_replaced():
    cv = projects_and_experience_cv()
    assert [s.id for s in cv.sections] == ["s1", "s2", "s3", "s4", "s5", "s6"]
    assert cv.sections[2].entries[0].bullets[1].id == "s3.e1.b2"

    tampered = cv.model_copy(deep=True)
    tampered.sections[0].id = "s4"
    tampered.sections[2].entries[0].id = "s4.e1"
    assert resume_model.assign_ids(tampered) == cv


def test_valid_selection_is_kept_in_order_without_notes():
    cv = projects_and_experience_cv()
    sections, notes = resume_model.check_selection(cv, pick(
        ("s4", [("s4.e1", ["s4.e1.b2", "s4.e1.b1"])]),
        ("s3", [("s3.e2", ["s3.e2.b1"]), ("s3.e1", ["s3.e1.b1"])]),
        EDU,
    ))
    assert notes == []
    assert [s.heading for s in sections] == ["Professional Experience", "Engineering Projects", "Education"]
    assert by_heading(sections)["Engineering Projects"] == ["ARMv7 Instruction-Level Emulator", "RLPredictor"]
    assert [b.id for b in sections[0].entries[0].bullets] == ["s4.e1.b2", "s4.e1.b1"]


def test_experience_listed_under_projects_is_moved_back():
    cv = projects_and_experience_cv()
    sections, notes = resume_model.check_selection(cv, pick(
        ("s3", [("s3.e1", ["s3.e1.b1"]), ("s4.e1", ["s4.e1.b1"])]),
        EDU,
    ))
    headings = by_heading(sections)
    assert headings["Engineering Projects"] == ["RLPredictor"]
    assert headings["Professional Experience"] == ["Freelance Web & Systems Developer"]
    assert any("Moved “Freelance Web & Systems Developer” back to “Professional Experience”" in n
               for n in notes)


def test_moved_entry_joins_a_section_the_model_also_listed():
    cv = projects_and_experience_cv()
    sections, _ = resume_model.check_selection(cv, pick(
        ("s4", [("s4.e2", [])]),
        ("s3", [("s4.e1", [])]),
        EDU,
    ))
    assert by_heading(sections)["Professional Experience"] == [
        "Circulation Senior Worker", "Freelance Web & Systems Developer"]
    assert "Engineering Projects" not in by_heading(sections)


def test_unknown_and_duplicate_ids_are_dropped():
    cv = projects_and_experience_cv()
    sections, notes = resume_model.check_selection(cv, pick(
        ("s9", [("s3.e1", ["s3.e1.b1"])]),
        ("s3", [("s3.e7", ["s3.e7.b1"]), ("s3.e1", ["s3.e1.b2"])]),
        EDU,
    ))
    assert by_heading(sections)["Engineering Projects"] == ["RLPredictor"]
    assert any("unknown section" in n for n in notes)
    assert any("unknown entry" in n for n in notes)
    assert any("repeat" in n for n in notes)


def test_bullet_from_another_entry_is_dropped():
    cv = projects_and_experience_cv()
    sections, notes = resume_model.check_selection(cv, pick(
        ("s3", [("s3.e2", ["s3.e2.b1", "s3.e1.b2", "s3.e2.b1"])]),
        EDU,
    ))
    assert [b.id for b in sections[0].entries[0].bullets] == ["s3.e2.b1"]
    assert any("'s3.e1.b2'" in n for n in notes)


def test_rewrite_is_applied_when_it_adds_no_numbers():
    cv = projects_and_experience_cv()
    sections, notes = resume_model.check_selection(cv, pick(
        ("s3", [("s3.e1", [("s3.e1.b2", "Raised prediction accuracy from 65% to 77.3% via feature work.")])]),
        EDU,
    ))
    assert notes == []
    assert sections[0].entries[0].bullets[0].text.startswith("Raised prediction accuracy")


@pytest.mark.parametrize("rewrite", [
    "Improved prediction accuracy from 65% to 81% through feature refinement.",
    "Designed an ML pipeline processing 10,000 replays of telemetry.",
])
def test_rewrite_with_invented_numbers_falls_back_to_cv_wording(rewrite):
    cv = projects_and_experience_cv()
    bullet = "s3.e1.b2" if "65%" in rewrite else "s3.e1.b1"
    sections, notes = resume_model.check_selection(cv, pick(
        ("s3", [("s3.e1", [(bullet, rewrite)])]), EDU))
    original = next(b.text for b in cv.sections[2].entries[0].bullets if b.id == bullet)
    assert sections[0].entries[0].bullets[0].text == original
    assert any("added numbers not in the CV" in n for n in notes)


def test_rewrite_may_use_numbers_from_the_entry_title():
    cv = projects_and_experience_cv()
    sections, notes = resume_model.check_selection(cv, pick(
        ("s3", [("s3.e2", [("s3.e2.b1", "Built an ARMv7 emulator in C++.")])]), EDU))
    assert notes == [] and sections[0].entries[0].bullets[0].text == "Built an ARMv7 emulator in C++."


def test_overlong_rewrite_is_rejected():
    cv = projects_and_experience_cv()
    sections, notes = resume_model.check_selection(cv, pick(
        ("s3", [("s3.e2", [("s3.e2.b1", "Built an emulator " + "and more " * 30)])]), EDU))
    assert sections[0].entries[0].bullets[0].text.startswith("Built an ARMv7 emulator modeling")
    assert any("much longer" in n for n in notes)


def test_missing_education_is_added():
    cv = projects_and_experience_cv()
    sections, notes = resume_model.check_selection(cv, pick(("s3", [("s3.e1", ["s3.e1.b1"])])))
    assert sections[-1].heading == "Education"
    assert sections[-1].entries[0].title.startswith("B.S.")
    assert any("Added “Education”" in n for n in notes)


def test_empty_list_entries_and_empty_sections_are_dropped():
    cv = projects_and_experience_cv()
    sections, _ = resume_model.check_selection(cv, pick(
        ("s2", [("s2.e1", ["s2.e1.b2"]), ("s2.e3", [])]),
        ("s6", []),
        EDU,
    ))
    assert by_heading(sections) == {"Technical Competencies": ["Languages"], "Education": [
        "B.S. Computer Science and Engineering"]}


def test_experience_only_cv_has_nowhere_to_misfile():
    cv = experience_only_cv()
    sections, notes = resume_model.check_selection(cv, pick(
        ("s1", [("s1.e1", ["s1.e1.b2"]), ("s1.e2", ["s1.e2.b1"])]),
        ("s3", [("s3.e1", [])]),
    ))
    assert notes == []
    assert [s.kind for s in sections] == ["experience", "education"]


def test_unmatched_bullets_flags_text_not_in_the_cv():
    cv = resume_model.assign_ids(ParsedCV(
        contact=ParsedContact(name="A", location=None, email=None, phone=None, links=[]),
        sections=[section("Projects", "projects", entry("Thing", [
            "Built a real-time\ncontrol system in C.",
            "Shipped to 1,000 users.",
        ]))],
    ))
    text = "PROJECTS\n**Thing**\n• Built a real-\ntime control system in C.\n"
    assert resume_model.unmatched_bullets(cv, text) == ["Projects › Thing: Shipped to 1,000 users."]


def test_prompt_view_lists_ids_but_not_contact():
    cv = projects_and_experience_cv()
    view = resume_model.describe_for_prompt(cv)
    assert 'SECTION s4 | heading: "Professional Experience" | kind: experience' in view
    assert "    s3.e1.b2: Improved average prediction accuracy" in view
    assert "test@example.com" not in view


def test_check_rewrite_ignores_thousands_separators():
    src = CVBullet(id="b", text="Served 1,000 users.")
    assert resume_model.check_rewrite(src, CVEntry(title="X"), "Served 1000 users.") is None
