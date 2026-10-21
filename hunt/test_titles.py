import unittest

from hunt.titles import excluded, role_matches, title_words

YES = [
    ("Full Stack Software Developer", "Full Stack Developer"),
    ("Full Stack Software Developer", "Senior Python Full Stack Engineer"),
    ("Full Stack Software Developer", "Fullstack Java Application Developer"),
    ("Full Stack Software Developer", "Senior Software Engineer- Java Full stack"),
    ("Full Stack Software Developer", "Hiring AI Full-Stack Engineer"),
    ("Forward Deployed Engineer", "Forward Deployed Engg. - Lead"),
    ("Forward Deployed Engineer", "Engineer, Forward Deployed"),
    ("Forward Deployed Engineer", "Forward-Deployed Software Engineer (FDE)"),
    ("Forward Deployed Engineer", "Senior FDE, Financial Services"),
    ("AI Engineer", "Applied AI/ML Engineer"),
    ("AI Engineer", "Software Engineer, AI Agents"),
    ("AI Engineer", "AI Native Software Engineer"),
    ("AI Engineer", "Gen AI/AI Engineer(NIT/IIT/BITS/IIIT)"),
    ("AI Engineer", "Full Stack AI Engineering Lead"),
    ("LLM Application Engineer", "Senior LLM Engineer"),
    ("Solutions Engineer", "Senior Solution Engineer"),
    ("Machine Learning Engineer", "Senior ML Engineer"),
    ("Backend Engineer", "Back-End Developer"),
]
NO = [
    ("AI Engineer", "Senior Software Engineer, Security Platform"),
    ("AI Engineer", "OpenAI Solutions Engineer"),
    ("Forward Deployed Engineer", "Fresher Accountant - Deployed To Petrol Pump Office"),
    ("Full Stack Software Developer", "Lead Software Engineer - .NET with Azure, React"),
    ("Software Engineer", "Account Executive"),
    ("Software Engineer", "Platform Engineer"),
    ("LLM Application Engineer", "Applications Engineer"),
]


class TitleTests(unittest.TestCase):
    def test_titles_that_should_match(self):
        for role, title in YES:
            self.assertTrue(role_matches(title, role), f"{role!r} should match {title!r}")

    def test_titles_that_should_not_match(self):
        for role, title in NO:
            self.assertFalse(role_matches(title, role), f"{role!r} should not match {title!r}")

    def test_variants_fold_together(self):
        self.assertEqual(title_words("Full-Stack Dev"), title_words("fullstack engineer"))
        self.assertEqual(title_words("GenAI Engineers"), ["generative", "ai", "engineer"])

    def test_exclusions_match_whole_words(self):
        self.assertTrue(excluded("Software Engineer Intern", ["intern"]))
        self.assertFalse(excluded("Internal Tools Engineer", ["intern"]))
        self.assertTrue(excluded("Senior Account Executive", ["account executive"]))
