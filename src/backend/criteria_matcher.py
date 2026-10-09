import json
import re
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def eval_job_criteria(scraped_job, criteria_path=None):
    """
        Desc:
            This function evaluates a scraped job description against a set of criteria defined in a JSON file.
        Args:
            scraped_job (dict): A dictionary containing the job description and other relevant information.
            criteria_path (str): The path to the JSON file containing the evaluation criteria.
        Returns:
            bool: True if the job meets the criteria, False otherwise.    
    """

    if criteria_path is None:
        criteria_path = os.path.join(BASE_DIR, "criteria.json")
    elif not os.path.isabs(criteria_path):
        criteria_path = os.path.join(BASE_DIR, criteria_path)

    with open(criteria_path, "r") as f:
        lim = json.load(f)


    # You can keep the function (scraped_job) as is. But don't rely on an actual webscraper such as playwright or anything, the user opens the page on their own as this is an extension, and the extension will scrape the page for them. So the function will be passed a dictionary of the scraped job description, title, location, pay, etc. The function will then evaluate the job description against the criteria defined in the JSON file and return a boolean value indicating whether the job meets the criteria or not.
    title = scraped_job.get("title", "").lower()
    location = scraped_job.get("location", "").lower()
    pay_txt = scraped_job.get("pay", "").lower()

    if any(token in title for token in lim["banned_tokens"]):
        return False, f"Job title contains banned token(s): {', '.join([token for token in lim['banned_tokens'] if token in title])}"

    if not any(token in title for token in lim["target_tokens"]):
        return False, f"Job title does not contain any target token(s): {', '.join(lim['target_tokens'])}"

    if not any(loc in location for loc in lim["allowed_locations"]):
        return False, f"Job location '{location}' is not in the list of allowed locations: {', '.join(lim['allowed_locations'])}"


    # Regex is great, for a scraper that is, but since we plan on migrating to a extension based application, we won't need to use regex to locate the pay.
    raw_nums = re.findall(r'\b\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\b\d{2,6}', pay_txt) 

    nums = [float(num.replace(',', '')) for num in raw_nums]

    if nums:
        max_pay = max(nums)
        is_hourly = "hour" in pay_txt or "hr" in pay_txt or max_pay < 500

        if is_hourly:
            if max_pay < lim["minimum_hourly_rate"]:
                return False, f"Job pay rate ${max_pay}/hr is below the minimum hourly rate of ${lim['minimum_hourly_rate']}/hr"
        else:
            if max_pay < lim["minimum_annual_salary"]:
                return False, f"Job salary ${max_pay}/yr is below the minimum annual salary of ${lim['minimum_annual_salary']}/yr"

    return True, "Job meets all criteria"


