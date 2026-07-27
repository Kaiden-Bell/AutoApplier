"""
test runner for criteria gate

"""
import json
from criteria_matcher import eval_job_criteria

def run_iso_test():

    with open("tests/test_cases.json", "r") as f:
        test_cases = json.load(f)

    passed = 0 
    failed = 0

    for i, case in enumerate(test_cases):
        name = case["name"]
        payload = case["payload"]
        expected_result = case["expected"]

        act_res, reason = eval_job_criteria(payload, criteria_path="criteria.json")

        try:
            assert act_res == expected_result, f"Expected {expected_result}, but got {act_res}. Reason: {reason}"
            passed += 1
        except AssertionError as e:
            print(f"Test case {i} failed: {e}")
            failed += 1

        print(f"Test #{i+1}: {name}")
        print(f"   [Input]    Title: {payload['title']} | Loc: {payload['location']} | Pay: {payload['pay']}")
    
    print(f"Tests passed: {passed}")
    print(f"Tests failed: {failed}")


if __name__ == "__main__":
    run_iso_test()