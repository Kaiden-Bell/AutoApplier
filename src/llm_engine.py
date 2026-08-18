"""
    LLM Engine and Parsing used to run the qualification check and code generation for the LaTeX tailored resume.
"""

import json
import os
from openai import OpenAI

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def run_llm_generation(prompt_path=None, cv_path=None, jd_path=None, output_path=None):
    """
        Desc:
            Analyzes job description and master CV using LLM.
            Generates a tailored resume in LaTeX format if qualified.
        Args:
            prompt_path (str): Path to the prompt file.
            cv_path (str): Path to the master CV file.
            jd_path (str): Path to the job description file.
            output_path (str): Path to the output LaTeX file.
        Returns:
            tuple: (bool, str) indicating success and the output path or gap analysis.
    """

    if prompt_path is None:
        prompt_path = os.path.join(BASE_DIR, "prompts", "resume_builder_prompt.txt")
    elif not os.path.isabs(prompt_path):
        prompt_path = os.path.join(BASE_DIR, prompt_path)

    if cv_path is None:
        cv_path = os.path.join(BASE_DIR, "tests", "test_text_files", "master_cv.txt")
    elif not os.path.isabs(cv_path):
        cv_path = os.path.join(BASE_DIR, cv_path)

    if jd_path is None:
        jd_path = os.path.join(BASE_DIR, "tests", "test_text_files", "job_description.txt")
    elif not os.path.isabs(jd_path):
        jd_path = os.path.join(BASE_DIR, jd_path)

    if output_path is None:
        output_path = os.path.join(BASE_DIR, "tailored_resume.tex")
    elif not os.path.isabs(output_path):
        output_path = os.path.join(BASE_DIR, output_path)

    client = OpenAI()

    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt = f.read()

    with open(cv_path, "r", encoding="utf-8") as f:
        master_cv_text = f.read()

    with open(jd_path, "r", encoding="utf-8") as f:
        job_description_text = f.read()

    user_content = f"""

    Please analyze the following job description and master CV to determine if the candidate is qualified for the position. 
    If qualified, generate a tailored resume in LaTeX format.

    [JOB DESCRIPTION]
    {job_description_text}

    [MASTER CV]
    {master_cv_text}

"""

    print("Sending request to LLM Engine...")

    response = client.chat.completions.create(
        model="gpt-5.4-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content}
        ]
    )

    response_text = response.choices[0].message.content
    response_parsed = json.loads(response_text)

    if not response_parsed["is_qualified"]:
        print(f"Skipping job. Reason: {response_parsed['gap_analysis']}")
        # Will write to a SQlite3 DB when testing is completed.
        return False, response_parsed["gap_analysis"]

    else: 
        print(f"Job is a match! Writing LaTeX file...")
        latex_code = response_parsed["latex_code"]

        # Will write to compile.py when testing is completed.
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(latex_code)
        
        return True, output_path

if __name__ == "__main__":
    run_llm_generation()





