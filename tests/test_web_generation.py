import csv
import io
import unittest

from app.services.case_generation import (
    cases_to_csv,
    cases_to_markdown,
    generate_web_cases_from_exploration,
)
from app.services.web_codegen import generate_pytest_bundle


def _sample_case():
    return {
        "id": "case-1",
        "title": "登录页可访问",
        "module": "登录",
        "owner": "",
        "preconditions": "测试环境可用",
        "steps": ["1. 打开页面", "2. 校验标题"],
        "expected": "页面正常展示",
        "related_work_item": "",
        "priority": "P0",
        "test_type": "功能测试",
        "executable_steps": [
            {"action": "navigate", "element": "https://example.test/login"},
            {"action": "assert", "element": "title", "expected": "登录"},
        ],
    }


class WebGenerationTests(unittest.TestCase):
    def test_cloud_case_exports_have_nine_columns(self):
        case = _sample_case()
        csv_rows = list(csv.reader(io.StringIO(cases_to_csv([case]))))
        self.assertEqual(len(csv_rows[0]), 9)
        self.assertEqual(cases_to_markdown([case]).splitlines()[0].count("|") - 1, 9)

    def test_rule_generation_keeps_exploration_evidence_and_executable_steps(self):
        exploration = {
            "start_url": "https://example.test/",
            "pages": [{
                "name": "00_initial",
                "url": "https://example.test/",
                "title": "首页",
                "depth": 0,
                "elements": [
                    {
                        "kind": "click", "blocked": "", "label": "登录",
                        "primary_locator": "role=link[name=\"登录\"]",
                        "locators": ["role=link[name=\"登录\"]"],
                    },
                    {
                        "kind": "input", "blocked": "", "label": "账号",
                        "primary_locator": "#username", "locators": ["#username"],
                    },
                ],
            }],
        }
        cases = generate_web_cases_from_exploration(exploration)
        self.assertGreaterEqual(len(cases), 1)
        self.assertTrue(all(case.get("executable") for case in cases))
        self.assertTrue(all(case["priority"] in {"P0", "P1", "P2", "P3"} for case in cases))

    def test_pytest_bundle_contains_pom_and_only_executable_cases(self):
        files = generate_pytest_bundle([_sample_case(), {"title": "无步骤", "priority": "P1"}])
        self.assertIn("conftest.py", files)
        self.assertIn("pages/generated_case_page.py", files)
        self.assertIn("testcases/test_generated_cases.py", files)
        self.assertIn("https://example.test/login", files["testcases/test_generated_cases.py"])


if __name__ == "__main__":
    unittest.main()
