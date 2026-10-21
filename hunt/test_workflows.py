import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunt.workflows import command, discover


class WorkflowTests(unittest.TestCase):
    def test_discover_reads_both_engines(self):
        root = Path(tempfile.mkdtemp())
        grok = root / ".grok" / "workflows"
        claude = root / ".claude" / "workflows"
        grok.mkdir(parents=True)
        claude.mkdir(parents=True)
        (grok / "resume-rewrite.rhai").write_text(
            'let meta = #{ name: "resume-rewrite", description: "Inventory a resume" };\n',
            encoding="utf-8",
        )
        (claude / "resume-rewrite.js").write_text(
            'export const meta = { name: "resume-rewrite", description: "Inventory a resume" };\n',
            encoding="utf-8",
        )
        found = {item.label(): item.description for item in discover(root)}
        self.assertEqual(found["grok:resume-rewrite"], "Inventory a resume")
        self.assertEqual(found["claude:resume-rewrite"], "Inventory a resume")

    def test_commands_target_the_matching_engine(self):
        root = Path(tempfile.mkdtemp())
        workflow = discover_one(root)
        payload = {"resume": "profile/og-resume.md"}
        with patch("hunt.workflows.shutil.which", side_effect=lambda name: f"/bin/{name}"):
            grok = command(workflow["grok"], root, payload)
            claude = command(workflow["claude"], root, payload)
        self.assertEqual(grok[0], "/bin/grok")
        self.assertIn("/workflow resume-rewrite", grok[-1])
        self.assertIn(json.dumps(payload, separators=(",", ":")), grok[-1])
        self.assertEqual(claude[0], "/bin/claude")
        self.assertIn("Workflow tool", claude[-1])
        self.assertIn(str(workflow["claude"].path), claude[-1])


def discover_one(root: Path) -> dict:
    grok = root / ".grok" / "workflows"
    claude = root / ".claude" / "workflows"
    grok.mkdir(parents=True)
    claude.mkdir(parents=True)
    (grok / "resume-rewrite.rhai").write_text(
        'let meta = #{ name: "resume-rewrite", description: "Grok rewrite" };\n',
        encoding="utf-8",
    )
    tex = claude / "resume-rewrite.js"
    tex.write_text(
        'export const meta = { name: "resume-rewrite", description: "Claude rewrite" };\n',
        encoding="utf-8",
    )
    return {item.engine: item for item in discover(root)}
