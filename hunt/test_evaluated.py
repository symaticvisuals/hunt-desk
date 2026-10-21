import unittest

from hunt.evaluated import classify, cleanup_rows, extract_job_id, name_key, normalize


def _row(**kwargs):
    base = {
        "date": "",
        "company": "",
        "role": "",
        "base_score": "",
        "penalties": "",
        "final_score": "",
        "applied": "",
        "reached_interview": "",
        "status": "",
        "platform": "",
        "job_id": "",
        "url": "",
        "source": "",
        "location": "",
    }
    base.update(kwargs)
    return base


class EvaluateTests(unittest.TestCase):
    def test_normalize_drops_the_and_punctuation(self):
        self.assertEqual(normalize("The Acme Co."), "acme co")
        self.assertEqual(name_key("The Acme Co.", "Senior Marketing Ops Manager"), name_key("Acme Co", "senior marketing ops manager"))

    def test_greenhouse_and_ashby_ids(self):
        greenhouse = "https://boards.greenhouse.io/figma/jobs/5426468004?gh_jid=5426468004"
        self.assertEqual(extract_job_id(greenhouse, "greenhouse"), "5426468004")
        ashby = "https://jobs.ashbyhq.com/linear/d3bc1ced-3ce4-4086-a050-555055dbb1ff"
        self.assertEqual(extract_job_id(ashby, "ashby"), "d3bc1ced-3ce4-4086-a050-555055dbb1ff")

    def test_in_batch_duplicate_by_id(self):
        candidates = [
            {
                "company": "Figma",
                "role": "Engineer",
                "url": "https://boards.greenhouse.io/figma/jobs/5?gh_jid=5",
                "platform": "greenhouse",
                "job_id": "",
            },
            {
                "company": "Figma Inc",
                "role": "Designer",
                "url": "https://boards.greenhouse.io/figma/jobs/5",
                "platform": "greenhouse",
                "job_id": "5",
            },
        ]
        self.assertEqual(classify(candidates, []), ["NEW", "DUP"])

    def test_cleanup_keeps_applied_yes(self):
        rows = [
            _row(company="Acme", role="Engineer", job_id="9", platform="greenhouse", applied=""),
            _row(company="The Acme", role="engineer", job_id="9", platform="greenhouse", applied="Yes"),
        ]
        kept, removed = cleanup_rows(rows)
        self.assertEqual(removed, 1)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["applied"], "Yes")


if __name__ == "__main__":
    unittest.main()
