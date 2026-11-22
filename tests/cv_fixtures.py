"""Parsed master CVs with different section layouts, for selection and render tests."""

from backend import resume_model
from backend.schemas import ParsedContact, ParsedCV, ParsedEntry, ParsedSection


def entry(title, bullets=(), organization=None, dates=None, location=None, tech=None):
    return ParsedEntry(title=title, organization=organization, dates=dates, location=location,
                       tech=tech, bullets=list(bullets))


def section(heading, kind, *entries):
    return ParsedSection(heading=heading, kind=kind, entries=list(entries))


def contact(name="Test Candidate"):
    return ParsedContact(name=name, location="Reno, NV", email="test@example.com",
                         phone="(555) 010-0000", links=["github.com/test-candidate"])


def projects_and_experience_cv():
    """Shaped like the real master CV: separate projects and experience sections."""
    return resume_model.assign_ids(ParsedCV(contact=contact(), sections=[
        section("Professional Profile", "summary", entry("", [
            "Computer Science and Engineering student focused on backend, full-stack, and "
            "systems development."])),
        section("Technical Competencies", "skills",
                entry("Languages", ["C/C++", "Python", "TypeScript", "SQL"]),
                entry("Backend & DevOps", ["FastAPI", "Node.js", "Docker"]),
                entry("Machine Learning", ["PyTorch", "scikit-learn"])),
        section("Engineering Projects", "projects",
                entry("RLPredictor", [
                    "Designed an end-to-end machine learning pipeline using replay telemetry.",
                    "Improved average prediction accuracy from 65% to 77.3% through feature refinement.",
                    "Implemented a conversational CLI for predictions and context.",
                ], tech="Python, PyTorch"),
                entry("ARMv7 Instruction-Level Emulator", [
                    "Built an ARMv7 emulator modeling registers and instruction decoding.",
                ], tech="C++"),
                entry("The Vault", [
                    "Built hardware-gated authentication over UART with an Arduino Mega & a 4×4 keypad.",
                ], tech="Python, Arduino C")),
        section("Professional Experience", "experience",
                entry("Freelance Web & Systems Developer", [
                    "Translate client requirements into digital products.",
                    "Architect applications using React, Astro, and FastAPI.",
                ], dates="2025 – Present"),
                entry("Circulation Senior Worker", [
                    "Assisted patrons with book-related queries.",
                ], organization="NSHE", dates="2023 – Present")),
        section("Education", "education",
                entry("B.S. Computer Science and Engineering", [],
                      organization="University of Nevada, Reno", dates="Aug 2023 – May 2027")),
        section("Languages", "languages", entry("", ["English (Proficient)", "Japanese (Intermediate)"])),
    ]))


def experience_only_cv():
    """A working professional: detailed experience, no projects section."""
    return resume_model.assign_ids(ParsedCV(contact=contact("Pat Example"), sections=[
        section("Work History", "experience",
                entry("Senior Backend Engineer", [
                    "Led migration of 40 services to Kubernetes.",
                    "Cut p99 latency by 30% on the billing API.",
                ], organization="Acme Corp", dates="2019 – 2024", location="Remote"),
                entry("Software Engineer", ["Built internal tooling in Go."],
                      organization="Widget Co", dates="2015 – 2019")),
        section("Skills", "skills", entry("Tools", ["Go", "Kubernetes", "PostgreSQL"])),
        section("Education", "education",
                entry("B.S. Computer Science", [], organization="State University", dates="2015")),
    ]))


def coursework_cv():
    """A new graduate with coursework and no projects or experience."""
    return resume_model.assign_ids(ParsedCV(contact=contact("Sam Student"), sections=[
        section("Education", "education",
                entry("B.S. Mathematics", ["GPA 3.9/4.0"], organization="Example College",
                      dates="May 2026")),
        section("Relevant Coursework", "coursework",
                entry("Mathematics", ["Linear Algebra", "Probability & Statistics"]),
                entry("Computing", ["Data Structures", "Databases"])),
        section("Skills", "skills", entry("Languages", ["Python", "R"])),
    ]))
