
"""Deterministic clinic tools backed by the supplied JSON files."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent
TODAY = date(2026, 11, 9)


def load_json(filename: str) -> Any:
    """Load a JSON file from the project directory."""
    path = DATA_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))


clinic = load_json("clinic.json")
patients = load_json("patients.json")
medications = load_json("medications.json")
appointments = load_json("appointments.json")


def find_patient(
    name: str,
    dob: str | None = None,
    contact: str | None = None,
) -> dict[str, Any]:
    """Find and verify a patient using the clinic's privacy policy."""
    normalized_name = name.strip().casefold()
    normalized_contact = (contact or "").strip().casefold()

    for patient in patients:
        name_matches = (
            normalized_name != ""
            and patient["name"].strip().casefold() == normalized_name
        )
        dob_matches = (
            dob is not None
            and patient["dob"] == dob
        )

        known_contacts = {
            patient.get("phone", "").strip().casefold(),
            patient.get("email", "").strip().casefold(),
        }
        known_contacts.discard("")

        contact_matches = (
            normalized_contact != ""
            and normalized_contact in known_contacts
        )
        caregiver = patient.get("authorised_caregiver") or {}
        caregiver_matches = (
            normalized_contact != ""
            and normalized_name == caregiver.get("name", "").strip().casefold()
            and normalized_contact == caregiver.get("phone", "").strip().casefold()
        )

        # The clinic policy accepts either name + DOB or a contact on file.
        if caregiver_matches:
            verified = True
        elif name_matches and dob_matches:
            verified = True
        elif contact_matches:
            verified = True
        else:
            continue

        return {
            "found": True,
            "verified": verified,
            "patient_id": patient["id"],
            "patient": patient if verified else None,
        }

    return {
        "found": False,
        "verified": False,
        "patient_id": None,
        "patient": None,
    }


def check_refill_eligibility(
    patient_id: str,
    medication_name: str,
) -> dict[str, Any]:
    """Apply the clinic's refill policy without approving a prescription."""
    patient = next(
        (p for p in patients if p["id"] == patient_id),
        None,
    )

    if patient is None:
        return {"eligible": False, "reason": "patient_not_found"}

    medication = next(
        (
            m for m in medications
            if m["patient_id"] == patient_id
            and m["medication"].casefold() == medication_name.strip().casefold()
        ),
        None,
    )

    if medication is None:
        return {"eligible": False, "reason": "medication_not_on_active_list"}

    if medication.get("controlled_schedule") is not None:
        return {
            "eligible": False,
            "reason": "controlled_substance",
            "route_to": "provider_refills",
        }

    last_visit = date.fromisoformat(patient["last_visit"])

    if last_visit < TODAY - timedelta(days=365):
        return {
            "eligible": False,
            "reason": "visit_required",
            "route_to": "provider_refills",
            "offer_appointment": True,
        }

    if medication["refills_remaining"] <= 0:
        return {
            "eligible": False,
            "reason": "no_refills_remaining",
            "route_to": "provider_refills",
        }

    last_filled = date.fromisoformat(medication["last_filled"])
    run_out_date = last_filled + timedelta(days=medication["days_supply"])
    earliest_request_date = run_out_date - timedelta(days=7)

    if TODAY < earliest_request_date:
        return {
            "eligible": False,
            "reason": "too_early",
            "earliest_request_date": earliest_request_date.isoformat(),
        }

    return {
        "eligible": True,
        "reason": "eligible_for_provider_approval",
        "patient_id": patient_id,
        "medication": medication["medication"],
        "prescriber": medication["prescriber"],
        "refills_remaining": medication["refills_remaining"],
        "requires_provider_approval": True,
    }


def route_message(queue: str, message_id: str) -> dict[str, str]:
    """Validate a destination queue and return a routing record."""
    if queue not in clinic["routing_queues"]:
        raise ValueError(f"Invalid routing queue: {queue}")

    return {
        "message_id": message_id,
        "routed_to": queue,
        "status": "queued",
    }


def get_patient_appointments(patient_id: str) -> list[dict[str, Any]]:
    """Return booked appointments belonging to one patient."""
    return [
        dict(appointment)
        for appointment in appointments
        if appointment.get("patient_id") == patient_id
        and appointment.get("status") == "booked"
        and appointment.get("date", "") >= TODAY.isoformat()
    ]


def _provider_available(provider_id: str, target_date: date) -> bool:
    """Check provider existence, clinic days, holidays, and leave."""
    if target_date < TODAY:
        return False
    provider = next(
        (p for p in clinic["providers"] if p["id"] == provider_id),
        None,
    )

    if provider is None:
        return False

    if target_date.strftime("%a") not in clinic["hours"]["days"]:
        return False

    if target_date.isoformat() in clinic["holidays"]:
        return False

    for start, end in provider.get("leave", []):
        if date.fromisoformat(start) <= target_date <= date.fromisoformat(end):
            return False

    return True


def _has_conflict(
    provider_id: str,
    target_date: date,
    start: datetime,
    end: datetime,
    exclude_id: str | None = None,
) -> bool:
    """Return True when an active appointment overlaps the requested time."""
    for appointment in appointments:
        if appointment["id"] == exclude_id:
            continue

        if appointment["status"] != "booked":
            continue

        if appointment["provider"] != provider_id:
            continue

        if appointment["date"] != target_date.isoformat():
            continue

        existing_start = datetime.strptime(appointment["start"], "%H:%M")
        existing_end = datetime.strptime(appointment["end"], "%H:%M")

        if start < existing_end and existing_start < end:
            return True

    return False


def find_available_slots(
    provider_id: str,
    visit_type: str,
    target_date: str,
) -> dict[str, Any]:
    """Find available slots for a provider on one date."""
    try:
        day = date.fromisoformat(target_date)
    except (TypeError, ValueError):
        return {"slots": [], "reason": "invalid_date"}

    if visit_type not in clinic["visit_types"]:
        return {"slots": [], "reason": "invalid_visit_type"}

    if not _provider_available(provider_id, day):
        return {"slots": [], "reason": "provider_unavailable"}

    duration = clinic["visit_types"][visit_type]
    opening = datetime.strptime(clinic["hours"]["open"], "%H:%M")
    closing = datetime.strptime(clinic["hours"]["close"], "%H:%M")
    lunch_start = datetime.strptime(clinic["hours"]["lunch"][0], "%H:%M")
    lunch_end = datetime.strptime(clinic["hours"]["lunch"][1], "%H:%M")

    slots = []
    current = opening

    while current + timedelta(minutes=duration) <= closing:
        minute = current.minute

        if minute in (0, 20, 40):
            end = current + timedelta(minutes=duration)

            overlaps_lunch = current < lunch_end and lunch_start < end
            conflicts = _has_conflict(provider_id, day, current, end)

            if not overlaps_lunch and not conflicts:
                slots.append({
                    "date": day.isoformat(),
                    "start": current.strftime("%H:%M"),
                    "end": end.strftime("%H:%M"),
                    "provider": provider_id,
                    "visit_type": visit_type,
                })

        current += timedelta(minutes=20)

    return {"slots": slots, "reason": "ok" if slots else "no_slots"}


def book_appointment(
    patient_id: str,
    provider_id: str,
    visit_type: str,
    target_date: str,
    start_time: str,
) -> dict[str, Any]:
    """Book a slot only if it is available."""
    if not any(p["id"] == patient_id for p in patients):
        return {"success": False, "reason": "patient_not_found"}

    available = find_available_slots(provider_id, visit_type, target_date)
    slot = next(
        (s for s in available["slots"] if s["start"] == start_time),
        None,
    )

    if slot is None:
        return {
            "success": False,
            "reason": available["reason"] if not available["slots"] else "slot_unavailable",
        }

    existing_numbers = [
        int(a["id"][1:])
        for a in appointments
        if a.get("id", "").startswith("A") and a["id"][1:].isdigit()
    ]
    appointment_id = f"A{max(existing_numbers, default=0) + 1:03d}"

    record = {
        "id": appointment_id,
        "patient_id": patient_id,
        "provider": provider_id,
        "date": slot["date"],
        "start": slot["start"],
        "end": slot["end"],
        "visit_type": visit_type,
        "status": "booked",
    }

    appointments.append(record)
    return {"success": True, "appointment": record}


def cancel_appointment(
    appointment_id: str,
    patient_id: str,
) -> dict[str, Any]:
    """Cancel an existing booked appointment."""
    appointment = next(
        (a for a in appointments if a["id"] == appointment_id),
        None,
    )

    if appointment is None:
        return {"success": False, "reason": "appointment_not_found"}

    if appointment["patient_id"] != patient_id:
        return {"success": False, "reason": "appointment_not_owned_by_patient"}

    if appointment["status"] != "booked":
        return {"success": False, "reason": "appointment_not_booked"}

    appointment["status"] = "cancelled"
    return {"success": True, "appointment": appointment}


def reschedule_appointment(
    appointment_id: str,
    patient_id: str,
    target_date: str,
    start_time: str,
) -> dict[str, Any]:
    """Move a booked appointment to an available slot."""
    appointment = next(
        (a for a in appointments if a["id"] == appointment_id),
        None,
    )

    if appointment is None:
        return {"success": False, "reason": "appointment_not_found"}

    if appointment["patient_id"] != patient_id:
        return {"success": False, "reason": "appointment_not_owned_by_patient"}

    if appointment["status"] != "booked":
        return {"success": False, "reason": "appointment_not_booked"}

    try:
        day = date.fromisoformat(target_date)
    except (TypeError, ValueError):
        return {"success": False, "reason": "invalid_date"}

    if not _provider_available(appointment["provider"], day):
        return {"success": False, "reason": "provider_unavailable"}

    try:
        start = datetime.strptime(start_time, "%H:%M")
    except ValueError:
        return {"success": False, "reason": "invalid_time"}

    duration = clinic["visit_types"].get(appointment["visit_type"])
    if duration is None:
        return {"success": False, "reason": "invalid_visit_type"}
    end = start + timedelta(minutes=duration)
    opening = datetime.strptime(clinic["hours"]["open"], "%H:%M")
    closing = datetime.strptime(clinic["hours"]["close"], "%H:%M")
    lunch_start = datetime.strptime(clinic["hours"]["lunch"][0], "%H:%M")
    lunch_end = datetime.strptime(clinic["hours"]["lunch"][1], "%H:%M")

    if start.minute not in (0, 20, 40):
        return {"success": False, "reason": "invalid_start_time"}

    if start < opening or end > closing:
        return {"success": False, "reason": "outside_clinic_hours"}

    if start < lunch_end and lunch_start < end:
        return {"success": False, "reason": "lunch_break"}

    if _has_conflict(
        appointment["provider"], day, start, end, exclude_id=appointment_id
    ):
        return {"success": False, "reason": "appointment_conflict"}

    appointment["date"] = day.isoformat()
    appointment["start"] = start.strftime("%H:%M")
    appointment["end"] = end.strftime("%H:%M")

    return {"success": True, "appointment": appointment}
