from datetime import date
import unittest

from src.workflow.leave_dialogue import LeaveDialogue, _extract_date_range


class LeaveDialogueTests(unittest.TestCase):
    def test_collects_missing_fields_before_confirmation(self):
        dialogue = LeaveDialogue()
        first = dialogue.handle("leave-1", "我要请3天年假")
        self.assertIsNotNone(first)
        self.assertEqual(first["status"], "collecting")
        self.assertIn("哪天开始", first["answer"])

        second = dialogue.handle("leave-1", "2026年8月3日开始")
        self.assertIsNotNone(second)
        self.assertEqual(second["status"], "ready_for_confirmation")
        self.assertEqual(second["action_card"]["start_date"], "2026-08-03")
        self.assertEqual(second["action_card"]["end_date"], "2026-08-05")

    def test_does_not_execute_without_confirmation_card(self):
        dialogue = LeaveDialogue()
        result = dialogue.handle("leave-2", "我要请病假，2026-08-03到2026-08-05")
        self.assertIsNotNone(result)
        self.assertEqual(result["action_card"]["type"], "leave_confirmation")
        self.assertEqual(result["tool_calls"], [])

    def test_extracts_relative_date(self):
        start, end = _extract_date_range("明天开始请假", today=date(2026, 8, 1))
        self.assertEqual(start, date(2026, 8, 2))
        self.assertIsNone(end)
