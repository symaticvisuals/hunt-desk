import json
import unittest
from pathlib import Path

from hunt.ats import check, failed, from_resume, from_tex, report, section_sizes
from hunt.latex import plain, render

SAMPLE = Path(__file__).resolve().parent / "testdata/sample-resume.json"
JOB = "We want checkout and payments depth, A/B testing, SQL, a roadmap owner, and discovery habits."


def _sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def _text() -> str:
    resume = _sample()
    header = resume["header"]
    lines = [header["name"], f"{header['location']} | {header['phone']} | {header['email']}", "Summary", resume["summary"], "Experience"]
    for entry in resume["experience"]:
        lines += [f"{entry['title']} {entry['dates']}", f"{entry['company']} {entry['location']}"]
        lines += [bullet["text"] for bullet in entry["bullets"]]
    lines.append("Projects")
    for project in resume["projects"]:
        lines.append(f"{project['name']} | {project['stack']} {project['dates']}")
        lines += [bullet["text"] for bullet in project["bullets"]]
    lines.append("Skills")
    lines += [f"{row['label']}: {row['items']}" for row in resume["skills"]]
    lines.append("Education")
    for row in resume["education"]:
        lines += [f"{row['school']} {row['location']}", f"{row['degree']} {row['dates']}"]
    return "\n".join(plain(line) for line in lines)


FACTS = from_resume(_sample())
MASTER = _text()


def _with_openings(word: str, count: int):
    bullets = list(FACTS.bullets)
    for index in range(count):
        bullets[index] = f"{word} {bullets[index]}"
    return FACTS._replace(bullets=bullets)


class AtsTests(unittest.TestCase):
    def test_a_clean_resume_passes_every_check(self):
        self.assertEqual(failed(check(_text(), 1, FACTS, JOB, MASTER)), [])

    def test_each_rule_fails_on_its_own_input(self):
        self.assertEqual(failed(check(_text(), 2, FACTS, JOB, MASTER)), ["One page"])
        self.assertEqual(failed(check(_text().replace("Beta Labs", ""), 1, FACTS, JOB, MASTER)), ["Text layer"])
        self.assertEqual(failed(check(_text() + " Responsible for tests.", 1, FACTS, JOB, MASTER)), ["Banned phrases"])
        self.assertEqual(failed(check(_text() + " Grew 97%.", 1, FACTS, JOB, MASTER)), ["New numbers"])
        self.assertEqual(failed(check(_text() + " [metric]", 1, FACTS, JOB, MASTER)), ["Placeholders"])
        duplicated = FACTS._replace(bullets=FACTS.bullets + FACTS.bullets[:1])
        self.assertEqual(failed(check(_text(), 1, duplicated, JOB, MASTER)), ["Duplicates"])
        self.assertEqual(failed(check(_text(), 1, _with_openings("Drove", 3), JOB, MASTER)), ["Openings"])
        self.assertEqual(failed(check(_text(), 1, FACTS, "unrelated posting", MASTER)), ["Keywords"])

    def test_no_phone_fails_contact_without_crashing(self):
        result = check(_text().replace("+91 98100 12345", ""), 1, FACTS, JOB, MASTER)
        self.assertEqual(failed(result), ["Contact"])
        self.assertIn("phone", report(result))

    def test_without_a_job_the_keyword_check_is_skipped(self):
        self.assertNotIn("Keywords", [item.name for item in check(_text(), 1, FACTS, None, MASTER)])

    def test_company_names_are_not_banned_phrases(self):
        facts = FACTS._replace(names=FACTS.names + ["Dynamic Yield"])
        text = _text() + "\nDynamic Yield Remote"
        self.assertEqual(failed(check(text, 1, facts, JOB, MASTER + " Dynamic Yield")), [])
        self.assertEqual(failed(check(text + " Responsible for tests.", 1, facts, JOB, MASTER + " Dynamic Yield")), ["Banned phrases"])

    def test_pdf_text_keeps_kerned_words_whole(self):
        from hunt.ats import pdf_text
        from hunt.pdf import compile_tex, find_tex

        if find_tex() is None:
            self.skipTest("no TeX engine on this machine")
        resume = {
            "candidate_level": "entry",
            "header": {"name": "Maya Rao", "email": "m@x.io", "phone": "+91 98450 11223"},
            "experience": [{"company": "Acme", "title": "PM", "dates": "2024", "bullets": [{"text": "Owned the roadmap.", "priority": 1}]}],
            "education": [{"school": "NIT Trichy", "location": "Trichy", "degree": "B.Tech", "dates": "2013 - 2017"}],
        }
        compiled = compile_tex(render(resume))
        self.assertIn("NIT Trichy", " ".join(pdf_text(compiled.pdf).split()))

    def test_facts_from_tex_match_the_rendered_resume(self):
        tex = render(_sample())
        facts = from_tex(tex)
        self.assertEqual(facts.names, FACTS.names)
        self.assertEqual(facts.headings, FACTS.headings)
        self.assertGreater(section_sizes(tex)["Experience"], section_sizes(tex)["Summary"])


if __name__ == "__main__":
    unittest.main()
