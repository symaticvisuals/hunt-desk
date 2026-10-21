import os
import tempfile
import unittest
from pathlib import Path

from hunt.claude_client import client_options, lookup_workspace, needs_workspace, store_workspace_id, workspace_required


class Workspace:
    def __init__(self, identifier: str, name: str, archived_at=None) -> None:
        self.id = identifier
        self.name = name
        self.archived_at = archived_at


class Workspaces:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.kwargs = None

    def list(self, **kwargs):
        self.kwargs = kwargs
        return type("Page", (), {"data": self.rows})()


class FakeClient:
    def __init__(self, rows) -> None:
        self.beta = self
        self.organization = self
        self.workspaces = Workspaces(rows)


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_key = os.environ.pop("ANTHROPIC_API_KEY", None)
        self.previous_workspace = os.environ.pop("ANTHROPIC_WORKSPACE_ID", None)
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        self._restore("ANTHROPIC_API_KEY", self.previous_key)
        self._restore("ANTHROPIC_WORKSPACE_ID", self.previous_workspace)

    def test_header_is_sent_only_when_a_workspace_is_saved(self):
        self.assertEqual(client_options(), {})
        os.environ["ANTHROPIC_WORKSPACE_ID"] = "wrkspc_saved"
        self.assertEqual(client_options(), {"default_headers": {"anthropic-workspace-id": "wrkspc_saved"}})

    def test_one_workspace_is_saved(self):
        client = FakeClient([Workspace("wrkspc_only", "Personal"), Workspace("wrkspc_old", "Old", archived_at="2026-01-01")])
        found = lookup_workspace(self.root, client=client)
        self.assertEqual(found.selected, "wrkspc_only")
        self.assertTrue(client.workspaces.kwargs["include_default"])
        self.assertIn("ANTHROPIC_WORKSPACE_ID=wrkspc_only", (self.root / ".env").read_text(encoding="utf-8"))

    def test_several_workspaces_wait_for_a_choice(self):
        client = FakeClient([Workspace("wrkspc_a", "Personal"), Workspace("wrkspc_b", "Work")])
        found = lookup_workspace(self.root, client=client)
        self.assertEqual(found.selected, "")
        self.assertEqual([item[0] for item in found.choices], ["wrkspc_a", "wrkspc_b"])
        self.assertFalse((self.root / ".env").exists())

    def test_saved_workspace_skips_the_list(self):
        store_workspace_id(self.root, "wrkspc_saved")

        class Boom:
            def list(self, **kwargs):
                raise AssertionError("list should not run")

        client = FakeClient([])
        client.workspaces = Boom()
        found = lookup_workspace(self.root, client=client)
        self.assertEqual(found.selected, "wrkspc_saved")

    def test_the_missing_header_error_is_recognized(self):
        message = "This API key is not scoped to a workspace, so this request must include the anthropic-workspace-id header"
        self.assertTrue(workspace_required(RuntimeError(message)))
        self.assertFalse(workspace_required(RuntimeError("model not found")))

    def _restore(self, name: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class ModelsClient:
    """Answers models.list, or raises what the API says for a key with no workspace."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        outer = self

        class Models:
            def list(self, **kwargs):
                outer.calls += 1
                if error is not None:
                    raise error
                return []

        self.models = Models()


class NeedsWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.pop("ANTHROPIC_WORKSPACE_ID", None)
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ.pop("ANTHROPIC_WORKSPACE_ID", None)
        if self.saved is not None:
            os.environ["ANTHROPIC_WORKSPACE_ID"] = self.saved

    def test_only_a_key_without_a_workspace_needs_one(self):
        root = Path(tempfile.mkdtemp())
        unscoped = ModelsClient(RuntimeError("This API key is not scoped to a workspace, so this request must include the anthropic-workspace-id header"))
        self.assertTrue(needs_workspace(root, client=unscoped))
        self.assertFalse(needs_workspace(root, client=ModelsClient()))
        self.assertFalse(needs_workspace(root, client=ModelsClient(RuntimeError("overloaded"))))

    def test_a_saved_workspace_skips_the_check(self):
        root = Path(tempfile.mkdtemp())
        os.environ["ANTHROPIC_WORKSPACE_ID"] = "wrkspc_1"
        client = ModelsClient(RuntimeError("not scoped to a workspace"))
        self.assertFalse(needs_workspace(root, client=client))
        self.assertEqual(client.calls, 0)

