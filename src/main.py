"""
    main.py - The main entry point for the AutoApplier application.
"""

import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from criteria_matcher import eval_job_criteria
from llm_engine import run_llm_generation
from compiler import compile_latex_to_pdf

def run_pipeline():
    print("--- Starting AutoApplier Pipeline ---")

    mock_job = {
        "title": "Software Engineer",
        "location": "Reno, NV",
        "pay": "$85,000 - $105,000 / year"
    }
    
    print(f"Checking Job Criteria for:\n   Title: {mock_job['title']}\n   Location: {mock_job['location']}\n   Pay: {mock_job['pay']}")
    
    meets_criteria, criteria_reason = eval_job_criteria(mock_job)
    print(f"Criteria Result: {meets_criteria} ({criteria_reason})")
    
    if not meets_criteria:
        print("Job does not meet criteria. Halting pipeline.")
        return

    print("\nCriteria check passed! Proceeding to LLM matching...")
    
    prompt_path = os.path.join(BASE_DIR, "prompts", "resume_builder_prompt.txt")
    cv_path = os.path.join(BASE_DIR, "tests", "test_text_files", "master_cv.txt")
    jd_path = os.path.join(BASE_DIR, "tests", "test_text_files", "job_description.txt")
    output_tex = os.path.join(BASE_DIR, "tailored_resume.tex")
    
    try:
        success, info = run_llm_generation(
            prompt_path=prompt_path,
            cv_path=cv_path,
            jd_path=jd_path,
            output_path=output_tex
        )
    except Exception as e:
        print(f"Error during LLM execution: {e}")
        print("Make sure you have set the OPENAI_API_KEY environment variable.")
        return

    if not success:
        print(f"Candidate not qualified. Gap analysis: {info}")
        return

    print(f"Tailored resume written to: {info}")

    print("\nCompiling tailored LaTeX resume...")
    compile_latex_to_pdf(output_tex)
    print("--- Pipeline Execution Finished ---")

if __name__ == "__main__":
    run_pipeline()