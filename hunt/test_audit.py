import unittest

from hunt.overleaf import project_id
from hunt.resume_audit import audit, latex_to_text, rewrite_bullet, rewrite_tex


TEX = r"""
\documentclass{article}
\begin{document}
\section{Experience}
\cventry{2024--now}{Engineer}{Acme}{}{}{}
\begin{itemize}
\item Responsible for shipping the billing service.
\item Cut deploy time by 40\%.
\end{itemize}
% TODO fill this in
\end{document}
"""


class AuditTests(unittest.TestCase):
    def test_latex_text_keeps_the_role_and_drops_commands(self):
        text = latex_to_text(TEX)
        self.assertIn("shipping the billing service", text)
        self.assertIn("Engineer", text)
        self.assertNotIn("documentclass", text)
        self.assertNotIn("TODO", text)

    def test_audit_flags_a_vague_bullet_and_a_missing_email(self):
        text = "- Responsible for shipping the billing service.\n- Cut deploy time.\n"
        codes = {finding.code for finding in audit(text)}
        self.assertIn("contact", codes)
        self.assertIn("vague", codes)
        self.assertIn("unquantified", codes)

    def test_humanizer_flags_a_contrast_and_keeps_a_date_dash(self):
        text = '- It is not a tool but a mirror.\n- The policy — announced in 2024 — ships.\n'
        codes = {finding.code for finding in audit(text)}
        self.assertIn("ai-tell", codes)
        rewritten = rewrite_bullet("The policy — announced in 2024 — ships. Dates 2024–2025 stayed.")
        self.assertNotIn("—", rewritten)
        self.assertIn("2024–2025", rewritten)

    def test_rewrite_does_not_invent_a_number(self):
        self.assertEqual(rewrite_bullet("Responsible for shipping the billing service."), "Shipping the billing service.")
        rewritten, count = rewrite_tex(TEX)
        self.assertEqual(count, 1)
        self.assertIn("Shipping the billing service.", rewritten)
        self.assertIn(r"\documentclass{article}", rewritten)
        self.assertIn("40", rewritten)

    def test_overleaf_url_yields_the_project_id(self):
        self.assertEqual(project_id("https://www.overleaf.com/project/68abc123def456"), "68abc123def456")
        self.assertEqual(project_id("https://git.overleaf.com/68abc123def456"), "68abc123def456")
        self.assertIsNone(project_id("/tmp/resume.pdf"))
