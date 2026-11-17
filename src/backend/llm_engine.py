"""
    LLM qualification check and tailored LaTeX resume generation.

    Uses OpenAI structured outputs so the response is validated into LLMResult rather than
    indexed as a loose dict.
"""

import logging
import os
from functools import lru_cache

import openai
from openai import OpenAI

from .config import PROMPT_PATH
from .schemas import Job, LLMResult

log = logging.getLogger(__name__)


class LLMError(Exception):
    """The model call failed, refused, or returned unusable output."""


@lru_cache(maxsize=1)
def _system_prompt():
    return PROMPT_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _openai_client():
    return OpenAI()


def _client():
    if not os.environ.get("OPENAI_API_KEY"):
        raise LLMError("OPENAI_API_KEY is not configured on the server.")
    return _openai_client()


def build_user_content(job, resume_text):
    header = "\n".join(
        f"{label}: {value}"
        for label, value in (("Title", job.title), ("Location", job.location), ("Pay", job.pay))
        if value
    )
    return f"""
Please analyze the following job description and master CV to determine if the candidate is qualified for the position.
If qualified, generate a tailored resume in LaTeX format.

[JOB DESCRIPTION]
{header}

{job.description}

[MASTER CV]
{resume_text}
"""


def generate(job: Job, resume_text: str, model: str) -> LLMResult:
    """
        Desc:
            Analyzes the job against the master CV and, if qualified, drafts a one-page LaTeX resume.
        Args:
            job (Job): Extracted job posting.
            resume_text (str): Master CV text.
            model (str): OpenAI model name.
        Returns:
            LLMResult: is_qualified, gap_analysis, latex_code ("" when not qualified).
        Raises:
            LLMError: on API failure, refusal, or missing/invalid structured output.
    """
    try:
        completion = _client().chat.completions.parse(
            model=model,
            response_format=LLMResult,
            messages=[
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": build_user_content(job, resume_text)},
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
