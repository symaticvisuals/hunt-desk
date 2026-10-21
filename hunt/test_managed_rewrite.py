import json
import tempfile
import unittest
from pathlib import Path

from hunt.managed_rewrite import SESSION_BUDGET_CENTS, ManagedError, load_ids, run_managed_rewrite
from hunt.workflows import command, discover


RESUME = """# Ada Lovelace
Engineer, 2019-2025

## Example Corp
Software engineer, 2022-2025
- Shipped the billing service in Python and Postgres.
"""


class Event:
    def __init__(self, kind: str, **kwargs) -> None:
        self.type = kind
        for key, value in kwargs.items():
            setattr(self, key, value)


class Stop:
    def __init__(self, kind: str) -> None:
        self.type = kind


class Download:
    def __init__(self, text: str) -> None:
        self.text = text

    def write_to_file(self, path: str) -> None:
        Path(path).write_text(self.text, encoding="utf-8")


class Stream:
    def __init__(self, events, order: list[str]) -> None:
        self._events = events
        self._order = order

    def __enter__(self):
        self._order.append("stream")
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self._events)


class FakeBeta:
    def __init__(self) -> None:
        self.order: list[str] = []
        self.created = None
        self.sent = []
        self.uploaded = None
        self.files = self
        self.sessions = self
        self.events = self

    def upload(self, *, file):
        self.uploaded = file.read()
        return type("File", (), {"id": "file_resume"})()

    def create(self, **kwargs):
        self.created = kwargs
        return type("Session", (), {"id": "sesn_test"})()

    def stream(self, *, session_id):
        self.order.append(f"stream:{session_id}")
        return Stream(
            [
                Event("agent.tool_use", id="tool_1", evaluated_permission="ask"),
                Event("session.status_idle", stop_reason=Stop("requires_action")),
                Event("session.status_idle", stop_reason=Stop("end_turn")),
            ],
            self.order,
        )

    def send(self, *, session_id, events):
        self.order.append("send")
        self.sent.extend(events)

    def list(self, *, scope_id, betas):
        self.order.append(f"list:{scope_id}:{betas[0]}")
        return type("Page", (), {"data": [type("Out", (), {"id": "file_out", "filename": "resume-rewrite.md"})()]})()

    def download(self, file_id):
        self.order.append(f"download:{file_id}")
        return Download("# Draft\n\nShipped the billing service in Python and Postgres.\n")


class FakeClient:
    def __init__(self) -> None:
        self.beta = FakeBeta()


def _root(lock: bool = True) -> Path:
    root = Path(tempfile.mkdtemp())
    profile = root / "profile"
    profile.mkdir()
    (profile / "og-resume.md").write_text(RESUME, encoding="utf-8")
    if lock:
        (root / "claude-lock.json").write_text(
            json.dumps(
                {
                    "resources": {
                        "./agents/resume-lead/agent.md": {"id": "agent_lead"},
                        "./environments/resume-rewrite.yaml": {"id": "env_resume"},
                    }
                }
            ),
            encoding="utf-8",
        )
    return root


class ManagedRewriteTests(unittest.TestCase):
    def test_session_uses_stored_ids_and_keeps_the_resume(self):
        root = _root()
        client = FakeClient()
        notes = []
        result = run_managed_rewrite(
            root,
            {"resume": "profile/og-resume.md", "job": "Backend engineer", "answers": "The job took 6 hours."},
            client=client,
            sleeper=lambda _seconds: None,
            on_progress=notes.append,
        )
        created = client.beta.created
        self.assertEqual(created["agent"], "agent_lead")
        self.assertEqual(created["environment_id"], "env_resume")
        self.assertEqual(created["resources"][0]["file_id"], "file_resume")
        self.assertEqual(created["resources"][0]["mount_path"], "/resume.md")
        self.assertEqual(created["budget"]["max_list_cost"]["amount"], SESSION_BUDGET_CENTS)
        self.assertNotIn("model", created)
        self.assertNotIn("system", created)
        self.assertNotIn("tools", created)
        self.assertEqual(client.beta.order[0], "stream:sesn_test")
        self.assertEqual(client.beta.order[1], "stream")
        self.assertEqual(client.beta.order[2], "send")
        self.assertEqual(client.beta.sent[0]["type"], "user.define_outcome")
        self.assertIn("/mnt/session/uploads/resume.md", client.beta.sent[0]["description"])
        self.assertIn("6 hours", client.beta.sent[0]["description"])
        self.assertEqual(client.beta.sent[1]["result"], "deny")
        self.assertIn("Shipped the billing service", result.path.read_text(encoding="utf-8"))
        self.assertEqual((root / "profile" / "og-resume.md").read_text(encoding="utf-8"), RESUME)
        self.assertIn("sesn_test", result.trace)
        self.assertIn(b"Ada Lovelace", client.beta.uploaded)
        self.assertTrue(any(line.startswith("Uploading") for line in notes))
        self.assertTrue(any(line.startswith("Tool ·") for line in notes))
        self.assertTrue(any(line.startswith("Downloading") for line in notes))

    def test_missing_lock_names_the_apply_command(self):
        root = _root(lock=False)
        with self.assertRaises(ManagedError) as caught:
            load_ids(root)
        self.assertIn("ant apply --dry-run agents environments", str(caught.exception))

    def test_agent_files_keep_the_model_split(self):
        root = Path(__file__).resolve().parents[1]
        lead = (root / "agents" / "resume-lead" / "agent.md").read_text(encoding="utf-8")
        inventory = (root / "agents" / "resume-inventory" / "agent.md").read_text(encoding="utf-8")
        rewrite = (root / "agents" / "resume-rewrite-worker" / "agent.md").read_text(encoding="utf-8")
        check = (root / "agents" / "resume-fact-check" / "agent.md").read_text(encoding="utf-8")
        self.assertIn("claude-opus-5-5", lead)
        self.assertIn("claude-haiku-5-5", inventory)
        self.assertIn("claude-sonnet-5-5", rewrite)
        self.assertIn("claude-sonnet-5-5", check)
        self.assertIn("../resume-inventory/agent.md", lead)
        self.assertIn("type: coordinator", lead)
        self.assertNotIn("multiagent_20261001", lead)
        found = {item.label() for item in discover(root)}
        self.assertIn("managed:resume-rewrite", found)
        managed = next(item for item in discover(root) if item.engine == "managed")
        with self.assertRaises(RuntimeError):
            command(managed, root, {"resume": "profile/og-resume.md"})
