"""
    LLM calls: parsing the master CV into sections, and qualification + resume selection.

    Both use OpenAI structured outputs, so responses are validated into pydantic models rather
    than indexed as loose dicts. The model never writes LaTeX: tailoring returns IDs drawn from
    the parsed CV (see resume_model.py), and render.py typesets them.
"""

import logging
import os
from functools import lru_cache

import openai
from openai import OpenAI

from . import resume_model
from .config import CV_PARSE_PROMPT_PATH, PROMPT_PATH
from .schemas import Job, MasterCV, ParsedCV, TailoredResume

log = logging.getLogger(__name__)


class LLMError(Exception):
    """The model call failed, refused, or returned unusable output."""


@lru_cache(maxsize=1)
def _system_prompt():
    return PROMPT_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _parse_prompt():
    return CV_PARSE_PROMPT_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _openai_client():
    return OpenAI()


def _client():
    if not os.environ.get("OPENAI_API_KEY"):
        raise LLMError("OPENAI_API_KEY is not configured on the server.")
    return _openai_client()


def build_user_content(job, master_cv: MasterCV):
    header = "\n".join(
        f"{label}: {value}"
        for label, value in (("Title", job.title), ("Location", job.location), ("Pay", job.pay))
        if value
    )
    return f"""
Decide whether the candidate is qualified for this position. If qualified, choose the CV content
for a tailored one-page resume by ID.

[JOB DESCRIPTION]
{header}

{job.description}

[MASTER CV]
{resume_model.describe_for_prompt(master_cv)}"""


def _structured_call(model, system, user, response_format):
    try:
        completion = _client().chat.completions.parse(
            model=model,
            response_format=response_format,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except openai.LengthFinishReasonError as e:
        raise LLMError("The model's response was cut off before it finished.") from e
    except openai.ContentFilterFinishReasonError as e:
        raise LLMError("The model's response was blocked by a content filter.") from e
    except openai.OpenAIError as e:
        log.warning("OpenAI request failed: %s", type(e).__name__)
        raise LLMError(f"The language model request failed ({type(e).__name__}).") from e

    if not completion.choices:
        raise LLMError("The model returned no choices.")
    message = completion.choices[0].message
    if message.refusal:
        raise LLMError(f"The model refused the request: {message.refusal}")
    if message.parsed is None:
        raise LLMError("The model returned output that did not match the expected format.")
    return message.parsed


def parse_cv(resume_text: str, model: str) -> MasterCV:
    """
        Desc:
            Splits the master CV text into typed sections, entries and bullets, keeping the
            CV's own headings and wording.
        Args:
            resume_text (str): Master CV text.
            model (str): OpenAI model name.
        Returns:
            MasterCV: The parse with position-derived IDs.
        Raises:
            LLMError: on API failure, refusal, invalid output, or a parse with no sections.
    """
    parsed = _structured_call(model, _parse_prompt(), f"[MASTER CV]\n{resume_text}", ParsedCV)
    if not parsed.sections:
        raise LLMError("The model found no sections in the CV.")
    try:
        return resume_model.assign_ids(parsed)
    except ValueError as e:  # pydantic limits on the stored form (too many sections, etc.)
        raise LLMError(f"The parsed CV is too large to store: {e}") from e


def generate(job: Job, master_cv: MasterCV, model: str) -> TailoredResume:
    """
        Desc:
            Judges the job against the master CV and, if qualified, selects CV content by ID for
            a one-page resume.
        Args:
            job (Job): Extracted job posting.
            master_cv (MasterCV): Parsed CV with IDs from resume_model.assign_ids.
            model (str): OpenAI model name.
        Returns:
            TailoredResume: is_qualified, gap_analysis, sections ([] when not qualified).
        Raises:
            LLMError: on API failure, refusal, or missing/invalid structured output.
    """
    return _structured_call(model, _system_prompt(), build_user_content(job, master_cv),
                            TailoredResume)
