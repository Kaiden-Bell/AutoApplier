# Auto Job Application Design Document

## Pipeline Architectue

| Job Listing | → | Parser | → | LLM Engine | → | LaTex Compiler | → | Human Review | 

## Tech Stack

- Orchestrator: Python
- APIs: Playwright (Checking website for linkedin/handshake)
- Engine Matching: OpenAI API
- DB/Storage: SQLite3
- LaTex Comp: Python-LaTex wrapper

## 1. Fetching Postings 

### Email scraper:

- A Python script using `imaplib` to find emails from handshake and linked in. Extracts the link for later use.     


### Browser Automation: 

- Using **Playwright** to periodcally search for target keywords "Software Engineering", "Fullstack", ... , and pulls raw HTML of jobs posts. 
 

## 2. Criteria Matching

Runs a determinestic check vs a text document.

- Parses **Location, Pay, Job Title** using regex
- Compares against the file `.json`. If the job requires specific relocation, or falls below certain thresholds, then don't pass the job through an LLM.

## 3. Comparison Check

If the critera check passes, then move a master CV and the job description to a LLM using a prompt such as:

- *"Analyze this [Master CV] against this [Job Description]. Output valid JSON with two fields: is_qualified (boolean) and gap_analysis (string analyzing missing requirements)*

- If the LLM returns is_qualifed = False, then log to the DB and mark it as "Skipped" and halt here.

## 4. Crafting a LaTex Resume

If the job matches the master CV, then we move to generating a resume to send the employer. 

[!NOTE] There must be a strict visual framework so the LLM doesnt flood the page.

- Inject a base template: Read a local file 
- Enforce constraints using another prompt: 

*"You must fit this content into exactly one page. Use concise bullet points focusing heavily on [X], [Y], and [Z]. Output raw LaTex code inside a JSON string block"*

## 5. Compilation and Human Review
DO NOT AUTO APPLY; allow yourself to review the generated resume, and cover letters for proper accuracy. 

- Preform local complication via python subprocess:
```python
import subprocess
subprocess.run(["pdflatex", "-output-directory=output/", "tailored_resume.txt"])
```

- Have the script open up the generated PDF automatically
- Review the PDF to ensure everything sounds correct.

