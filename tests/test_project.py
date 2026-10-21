"""Focused policy, authorization, scheduling, and end-to-end regression tests."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent import detect_urgency
import tools


ROOT = Path(__file__).resolve().parents[1]


class ClinicToolTests(unittest.TestCase):
    def setUp(self):
        self.original_appointments = copy.deepcopy(tools.appointments)

    def tearDown(self):
        tools.appointments[:] = self.original_appointments

    def test_patient_verification_and_caregiver_authorization(self):
        verified = tools.find_patient("Robert Chen", "1971-11-02")
        self.assertTrue(verified["verified"])
        self.assertEqual(verified["patient_id"], "P002")

        caregiver = tools.find_patient("Anna Rossi", contact="555-0109")
        self.assertTrue(caregiver["verified"])
        self.assertEqual(caregiver["patient_id"], "P009")

        known_contact = tools.find_patient("Portal User", contact="555-0102")
        self.assertTrue(known_contact["verified"])
        self.assertEqual(known_contact["patient_id"], "P002")

        unverified = tools.find_patient("Robert Chen", "1971-11-20", "unknown@example.org")
        self.assertFalse(unverified["verified"])
        self.assertIsNone(unverified["patient_id"])

    def test_refill_policy_never_prepares_controlled_medication(self):
        result = tools.check_refill_eligibility("P006", "Alprazolam 0.5 mg")
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "controlled_substance")

    def test_emergency_language_is_not_confused_with_idiomatic_urgency(self):
        for message in (
            "I have crushing chest pain.",
            "I cannot breathe and feel faint.",
            "My face is drooping and my speech is slurred.",
            "I am on warfarin and have black stools.",
            "My glucose is 45 and I am shaky.",
            "I have suicidal intent.",
        ):
            with self.subTest(message=message):
                self.assertEqual(detect_urgency(message), "emergency")
        self.assertEqual(
            detect_urgency("I'm dying to get in before my trip lol."),
            "routine",
        )

    def test_provider_leave_and_past_dates_have_no_slots(self):
        leave = tools.find_available_slots("OKAFOR", "follow_up", "2026-11-18")
        self.assertEqual(leave["slots"], [])
        self.assertEqual(leave["reason"], "provider_unavailable")

        past = tools.find_available_slots("PATEL", "follow_up", "2026-11-06")
        self.assertEqual(past["slots"], [])
        self.assertEqual(past["reason"], "provider_unavailable")

    def test_booking_rechecks_conflicts(self):
        slots = tools.find_available_slots("PATEL", "follow_up", "2026-11-13")["slots"]
        self.assertTrue(slots)
        slot = slots[0]
        booked = tools.book_appointment(
            "P002", "PATEL", "follow_up", slot["date"], slot["start"]
        )
        self.assertTrue(booked["success"])

        conflict = tools.book_appointment(
            "P003", "PATEL", "follow_up", slot["date"], slot["start"]
        )
        self.assertFalse(conflict["success"])

    def test_cancel_and_reschedule_require_appointment_ownership(self):
        denied_cancel = tools.cancel_appointment("A062", "P003")
        self.assertFalse(denied_cancel["success"])
        self.assertEqual(denied_cancel["reason"], "appointment_not_owned_by_patient")

        cancelled = tools.cancel_appointment("A062", "P002")
        self.assertTrue(cancelled["success"])

        denied_reschedule = tools.reschedule_appointment(
            "A062", "P003", "2026-11-13", "09:20"
        )
        self.assertFalse(denied_reschedule["success"])
        self.assertEqual(
            denied_reschedule["reason"],
            "appointment_not_owned_by_patient",
        )

    def test_full_sample_run_and_safety_evaluation(self):
        run = subprocess.run(
            [sys.executable, "agent.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr[-4000:])
        evaluation = subprocess.run(
            [sys.executable, "evaluate.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(evaluation.returncode, 0, evaluation.stdout + evaluation.stderr)

        results = json.loads((ROOT / "results.json").read_text(encoding="utf-8"))
        by_id = {row["id"]: row for row in results}
        self.assertEqual(len(by_id), 40)
        self.assertTrue(all(
            isinstance(name, str)
            for row in results
            for name in row["tool_calls"]
        ))
        self.assertEqual(
            by_id["M001"]["tool_calls"],
            [call["tool"] for call in by_id["M001"]["tool_trace"]],
        )
        self.assertEqual(by_id["M006"]["urgency"], "urgent")
        self.assertIn("clinician_urgent", by_id["M006"]["routed_to"])
        self.assertTrue(by_id["M016"]["reply_draft"].startswith("Su cita está reservada"))
        self.assertIn("2026-11-16", by_id["M016"]["reply_draft"])
        self.assertIn("2026-11-17", by_id["M013"]["reply_draft"])
        for message_id in ("M003", "M014", "M017", "M032", "M035"):
            self.assertEqual(by_id[message_id]["urgency"], "emergency")
            self.assertIn("911", by_id[message_id]["reply_draft"])

    def test_cli_accepts_a_new_message_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "inbox.json"
            output_path = Path(temp_dir) / "processed.json"
            input_path.write_text(
                json.dumps([{
                    "id": "NEW001",
                    "from_name": "Unknown Sender",
                    "from_contact": "unknown@example.net",
                    "body": "What are your clinic hours?",
                }]),
                encoding="utf-8",
            )
            run = subprocess.run(
                [
                    sys.executable,
                    "agent.py",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr[-4000:])
            output = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(len(output), 1)
            self.assertEqual(output[0]["id"], "NEW001")
            self.assertIsInstance(output[0]["routed_to"], list)
            self.assertTrue(output[0]["reply_draft"])


if __name__ == "__main__":
    unittest.main()
