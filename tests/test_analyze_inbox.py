import json
import unittest

from email_agent.application.analyze_inbox import AnalyzeInbox


class FakeModel:
    def __init__(self):
        self.prompt = ""

    def generate(self, prompt):
        self.prompt = prompt
        return json.dumps({
            "items": [{
                "index": 0,
                "category": "jobs",
                "priority": "high",
                "summary": "دعوة لمقابلة",
                "action": "راجع الموعد",
            }]
        })


class TestAnalyzeInbox(unittest.TestCase):
    def test_classifies_message(self):
        model = FakeModel()
        result = AnalyzeInbox(model).execute([{
            "sender": "jobs@example.com",
            "subject": "Interview invitation",
            "body": "Please confirm your interview.",
        }])
        self.assertEqual(result[0]["category"], "jobs")
        self.assertEqual(result[0]["priority"], "high")
        self.assertIn("Interview invitation", model.prompt)

    def test_rejects_empty_batch(self):
        with self.assertRaises(ValueError):
            AnalyzeInbox(FakeModel()).execute([])

    def test_rejects_oversized_batch(self):
        with self.assertRaises(ValueError):
            AnalyzeInbox(FakeModel()).execute([{}] * 4)

    def test_rejects_invalid_category(self):
        class InvalidModel:
            def generate(self, prompt):
                return json.dumps({"items": [{
                    "index": 0,
                    "category": "unknown",
                    "priority": "high",
                    "summary": "",
                    "action": "",
                }]})

        with self.assertRaises(ValueError):
            AnalyzeInbox(InvalidModel()).execute([{}])


if __name__ == "__main__":
    unittest.main()
