"""
Date: 07/24
Author: Kaiden Bell

Description: LLM Engine and Parsing used to run the qualification check and code generation for the LaTeX tailored resume.
"""

import json
import os
from openai import OpenAI

client = OpenAI()

with open("prompts/resume_builder_prompt.txt", "r") as f:
    prompt = f.read()

with open("test_text_files/master_cv.txt", "r", encoding="utf-8") as f:
    master_cv_text = f.read()

with open("test_text_files/job_description.txt", "r", encoding="utf-8") as f:
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

else: 
    print(f"Job is a match! Writing LaTeX file...")
    latex_code = response_parsed["latex_code"]

    # Will write to compile.py when testing is completed.
    with open("tailored_resume.tex", "w") as f:
        f.write(latex_code)





