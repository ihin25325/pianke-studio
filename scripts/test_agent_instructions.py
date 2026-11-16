"""Verify the repository's required change-tracking and delivery instructions."""
from pathlib import Path
import unittest


class AgentInstructionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instructions = (Path(__file__).resolve().parents[1] / 'AGENTS.md').read_text(encoding='utf-8')

    def test_each_change_requires_a_git_commit(self):
        self.assertIn('每次改动完成后，都必须创建一个对应的Git commit，以便后续追踪和回滚。', self.instructions)

    def test_tests_must_be_updated_and_pass_before_delivery(self):
        self.assertIn('每次改动后，都必须编写或更新相关测试，并在交付给用户前，确保所有测试和验证全部通过。', self.instructions)


if __name__ == '__main__':
    unittest.main()
