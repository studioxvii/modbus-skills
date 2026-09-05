from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from summarize_skill_output import measure_transcript


def message(identifier, text, phase="final_answer"):
    return {"method": "item/completed", "params": {"threadId": "a", "item": {
        "type": "agentMessage", "id": identifier, "text": text, "phase": phase}}}


def usage(thread, value):
    return {"method": "thread/tokenUsage/updated", "params": {
        "threadId": thread, "tokenUsage": {"total": {"totalTokens": value}}}}


class OutputMeasurementTests(unittest.TestCase):
    def test_cumulative_usage_is_not_added_repeatedly_and_restart_is_added(self):
        got = measure_transcript([usage("a", 20), usage("a", 70), usage("b", 10)])
        self.assertEqual(got["token_usage"]["totalTokens"], 80)
        self.assertIsNone(got["token_usage"]["outputTokens"])

    def test_missing_thread_usage_is_unknown_not_zero(self):
        got = measure_transcript([usage("b", 10), message("1", "Hello")])
        self.assertIsNone(got["token_usage"]["totalTokens"])

    def test_completed_messages_count_once_and_every_turn_is_visible(self):
        first = message("1", "Which input?")
        got = measure_transcript([first, first, message("2", "Working now", "commentary"),
                                  message("3", "Done [file](output.csv)")])
        self.assertEqual(got["visible_total"]["messages"], 3)
        self.assertEqual(got["visible_total"]["words"], 6)
        self.assertEqual(got["final_answers_all_turns"]["words"], 4)
        self.assertEqual(got["last_final_answer"]["words"], 2)
        self.assertEqual(got["last_final_answer"]["markdown_link_occurrences"], 1)

    def test_delta_events_do_not_double_count_and_utf8_bytes_are_explicit(self):
        got = measure_transcript([{"method": "item/agentMessage/delta", "params": {"delta": "é"}}, message("1", "é")])
        self.assertEqual(got["visible_total"]["utf8_bytes"], 2)

    def test_no_events_has_unknown_usage(self):
        got = measure_transcript([])
        self.assertIsNone(got["token_usage"]["totalTokens"])
        self.assertEqual(got["visible_total"]["words"], 0)

    def test_completed_native_sleep_is_counted_once_without_elapsed_claim(self):
        event = {"method": "item/completed", "params": {"threadId": "a", "item": {
            "type": "sleep", "id": "wait", "durationMs": 30000}}}
        got = measure_transcript([event, event])
        self.assertEqual(got["completed_native_sleep_calls"], 1)
        self.assertEqual(got["requested_native_sleep_ms"], 30000)


if __name__ == "__main__":
    unittest.main()
