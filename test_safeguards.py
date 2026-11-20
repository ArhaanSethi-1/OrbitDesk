import ast
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

import safeguards


class SafeguardPolicyTests(unittest.TestCase):
    def test_every_always_allowed_action_is_checked(self):
        targeted_cases = {
            "open_approved_app": "calculator",
            "open_approved_folder": "project",
            "open_approved_website": "https://www.google.com",
        }
        for action in safeguards.ALWAYS_ALLOWED:
            with self.subTest(action=action):
                decision, _ = safeguards.authorize(action, target=targeted_cases.get(action))
                self.assertEqual(decision, safeguards.Decision.ALLOW)

    def test_approved_open_actions_reject_unlisted_targets(self):
        cases = (
            ("open_approved_app", "powershell"),
            ("open_approved_folder", "system32"),
            ("open_approved_website", "https://example.invalid"),
        )
        for action, target in cases:
            with self.subTest(action=action):
                decision, _ = safeguards.authorize(action, target=target)
                self.assertEqual(decision, safeguards.Decision.BLOCK)

    def test_desktop_actions_require_a_user_decision(self):
        actions = (
            "type_text", "prompt_external_ai", "launch_program", "system_control",
            "volume_control", "run_approved_command", "navigate_website",
            "move_file",
        )
        with patch.object(safeguards, "_always_granted", return_value=set()):
            for action in actions:
                with self.subTest(action=action):
                    decision, _ = safeguards.authorize(action, target="test")
                    self.assertEqual(decision, safeguards.Decision.APPROVAL_REQUIRED)

    def test_outside_write_path_requires_explicit_approval(self):
        target = Path(tempfile.gettempdir()) / "orbitdesk-user-approved-test.txt"
        with patch.object(safeguards, "_always_granted", return_value=set()):
            decision, _ = safeguards.authorize("create_file", target=str(target))
            self.assertEqual(decision, safeguards.Decision.APPROVAL_REQUIRED)
            decision, _ = safeguards.authorize("create_file", target=str(target), approved=True)
            self.assertEqual(decision, safeguards.Decision.ALLOW)

    def test_brain_authorize_calls_are_classified(self):
        source = Path(__file__).with_name("brain.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        used_actions = set()

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "authorize":
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                if isinstance(node.args[0].value, str):
                    used_actions.add(node.args[0].value)

        configured_actions = (
            safeguards.ALWAYS_ALLOWED
            | safeguards.REQUIRES_APPROVAL
            | safeguards.ALWAYS_BLOCKED
        )
        self.assertLessEqual(used_actions, configured_actions)

    def test_action_categories_do_not_overlap(self):
        groups = (
            safeguards.ALWAYS_ALLOWED,
            safeguards.REQUIRES_APPROVAL,
            safeguards.ALWAYS_BLOCKED,
        )
        for index, group in enumerate(groups):
            for other in groups[index + 1:]:
                self.assertTrue(group.isdisjoint(other))

    def test_project_file_is_inside_read_boundary(self):
        allowed, _ = safeguards.check_read_path(__file__)
        self.assertTrue(allowed)

    def test_output_path_is_inside_write_boundary(self):
        output_file = Path(__file__).with_name("output") / "test-result.txt"
        allowed, _ = safeguards.check_write_path(output_file)
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
