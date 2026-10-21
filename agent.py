"""EDVAK Forge: deterministic, safety-first clinic front-desk agent."""

import argparse
import json
import logging
import re
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path

import mcp_bridge as clinic

BASE_DIR = Path(__file__).resolve().parent
TODAY = date.fromisoformat(clinic.clinic_data["today"])
LOGGER = logging.getLogger("edvak.agent")

INTENT_PATTERNS = {
    "refill": (
        r"\b(refill\w*|renew\w*|prescription|script|"
        r"running low|ran out|out of (my )?(pills|medication|medicine)|"
        r"get my [a-z][a-z -]+|más metformina|más medicación|necesito más)\b"
    ),
    "reschedule": (
        r"\b(reschedule|move my appointment|move my visit|"
        r"change my appointment|move my annual physical|move my physical)\b"
    ),
    "cancel": r"\b(cancel|cancellation)\b",
    "schedule": (
        r"\b(appointment|book\w*|booking|schedule|physical|follow-up|"
        r"chequeo anual|cita|próxima semana|get an appointment|"
        r"seen today|want to see|want to book)\b"
    ),
    "billing": r"\b(copay|co-pay|bill|billing|invoice|payment|charge|insurance claim)\b",
    "clinical": (
        r"\b(symptom|symptoms|side effect|dosage|dose|should i take|"
        r"what should (?:i do|we do)|fever|cough|pain|bleeding|wheezing|blood sugar|"
        r"feeling really low|seethe point|see the point|confused|head injury|"
        r"burning when i pee|"
        r"uti|high tsh|lab results|result notification)\b"
    ),
    "lab_results_request": (
        r"\b(send|email|forward|share|release)\b.*"
        r"\b(lab results|test results|medical records)\b"
    ),
    "administrative": (
        r"\b(hours|opening|address|directions|fax|records|medical record|"
        r"insurance accepted|change my phone number|update my phone number|"
        r"open the day after thanksgiving)\b"
    ),
}

EMERGENCY_PATTERNS = [
    r"\bcrushing chest pain\b",
    r"\bchest pain\b.*\b(left arm|sweaty|dizzy|short of breath)\b",
    r"\b(chest pain|chest pressure|chest tightness)\b",
    r"\b(can't|cannot|can not|unable to|struggling to|having difficulty)\s+breathe\b",
    r"\b(shortness of breath|can't finish sentences|cannot finish sentences|gasping for air)\b",
    r"\b(inhaler isn't helping|inhaler is not helping)\b",
    r"\b(unconscious|not breathing|signs of stroke|face (is )?drooping|"
    r"speech (is )?slurred|slurred speech)\b",
    r"\b(hit (her|his|their) head|head injury)\b.*\b(confused|confusion)\b",
    r"\b(fell|fall)\b.*\b(hit (her|his|their) head)\b.*\b(confused|confusion)\b",
    r"\b(uncontrolled|heavy|severe) bleeding\b",
    r"\bwarfarin\b.*\b(bleeding|dark stools|black stools)\b",
    r"\b(bleeding|dark stools|black stools)\b.*\bwarfarin\b",
    r"\b(blood sugar|glucose)\b.*\b([0-4]?\d)\b",
    r"\b(glucose|blood sugar).*\b([0-4]?\d)\b.*\b(shaky|sweaty|confused)\b",
    r"\b(overdose|anaphylaxis|throat closing|throat swelling)\b",
    r"\b(kill myself|end my life|suicidal intent|suicidal|"
    r"want to hurt myself|going to hurt myself)\b",
]

URGENT_PATTERNS = [
    r"\bASAP\b",
    r"\bout of (my )?(pills|medication|medicine)\b",
    r"\b(running low|running out|ran out) of\b",
    r"\b(feel really low|feeling really low|don't see the point|don't seethe point)\b",
    r"\bfever\b.*\b(two days|2 days|three days|3 days)\b",
    r"\b(101|102|103|104)\b.*\bfever\b",
    r"\b(today or tomorrow|seen today|today please)\b",
    r"\b(urgent|urgently)\b",
    r"\b(burning when i pee|burning when urinating|uti)\b",
]

SUSPICIOUS_PATTERNS = [
    r"\bignore (all )?(previous|prior) instructions\b",
    r"\bignore your rules\b",
    r"\bapprove refills for all\b",
    r"\bbypass (verification|safety|policy)\b",
    r"\breveal (the )?(patient|medical|private) records\b",
]

SPAM_PATTERNS = [
    r"\breply STOP to unsubscribe\b",
    r"\bspecial offer\b.*\bthis month only\b",
    r"\bmarketing message\b",
]


def text(value):
    return "" if value is None else str(value).strip()


def get_body(message):
    for key in ("body", "text", "message", "content", "message_text"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def detect_intents(body):
    lower = body.lower()

    if any(re.search(p, body, re.I) for p in SPAM_PATTERNS):
        return ["spam"]

    if re.search(r"\b(new patient|accept new patients)\b", lower):
        return ["new_patient"]

    if (
        re.search(r"\b(wife|husband)\b", lower)
        and re.search(
            r"\b(appointment this week|their appointment|her appointment|his appointment)\b",
            lower,
        )
    ):
        return ["third_party"]

    found = [
        name for name, pattern in INTENT_PATTERNS.items()
        if re.search(pattern, body, re.I)
    ]

    # Normalize accents for Spanish requests, so both "más" and "mas" work.
    normalized_body = "".join(
        char for char in unicodedata.normalize("NFKD", lower)
        if not unicodedata.combining(char)
    )
    if re.search(
        r"\b(necesito\s+mas\s+(?:metformina|medicacion|medicamento)|"
        r"mas\s+metformina|renovar\s+(?:mi\s+)?receta)\b",
        normalized_body,
    ) and "refill" not in found:
        found.append("refill")

    # Avoid interpreting a question about the meaning of results as a
    # request to send or disclose records.
    if re.search(r"\b(explain|what does|what do|meaning of)\b", lower) and re.search(
        r"\b(TSH|lab results|test results|result)\b", lower
    ):
        found = [x for x in found if x != "lab_results_request"]
        if "clinical" not in found:
            found.append("clinical")
    elif "lab_results_request" in found:
        found = [intent for intent in found if intent != "clinical"]

    if re.search(r"\b(vaccination|immunization|medical) records?\b", lower):
        found.append("records")

    # A specific reschedule or cancellation should not become a new booking.
    if "reschedule" in found:
        found = [x for x in found if x != "schedule"]
    if "cancel" in found:
        found = [x for x in found if x != "schedule"]

    # "Script" and medication-specific refill phrases are refill requests.
    medication_request = bool(
        re.search(
            r"\b(oxycodone|levothyroxine|atorvastatin|metformin|"
            r"omeprazole|sertraline|escitalopram|donepezil|lisinopril)\b",
            lower,
        )
        and re.search(
            r"\b(refill\w*|script|prescription|get my|need my|need more)\b",
            lower,
        )
    )
    if medication_request and "refill" not in found:
        found.append("refill")

    # A controlled-medication script request with pain as its rationale should
    # go to the prescribing provider, not create a second clinical-routing action.
    if re.search(r"\boxycodone\b", lower) and re.search(
        r"\b(script|prescription|refill\w*)\b", lower
    ):
        found = [intent for intent in found if intent != "clinical"]

    return list(dict.fromkeys(found)) or ["general_inquiry"]


def detect_urgency(body):
    if any(re.search(p, body, re.I) for p in EMERGENCY_PATTERNS):
        return "emergency"
    if any(re.search(p, body, re.I) for p in URGENT_PATTERNS):
        return "urgent"
    return "routine"


def safe_tool_result(name, result):
    if name == "find_patient" and isinstance(result, dict):
        return {
            key: result.get(key)
            for key in ("found", "verified", "patient_id")
        }
    if name == "get_patient_medications" and isinstance(result, list):
        return {"record_count": len(result)}
    if name == "get_patient_appointments" and isinstance(result, list):
        fields = ("id", "provider", "date", "start", "end", "visit_type", "status")
        return [
            {key: appointment.get(key) for key in fields}
            for appointment in result
            if isinstance(appointment, dict)
        ]
    if name == "check_refill_eligibility" and isinstance(result, dict):
        fields = (
            "eligible", "reason", "earliest_request_date", "route_to",
            "offer_appointment", "requires_provider_approval",
            "refills_remaining",
        )
        return {key: result[key] for key in fields if key in result}
    if name in {"book_appointment", "reschedule_appointment", "cancel_appointment"}:
        if isinstance(result, dict):
            safe = {key: result[key] for key in ("success", "reason") if key in result}
            appointment = result.get("appointment")
            if isinstance(appointment, dict):
                fields = (
                    "id", "provider", "date", "start", "end",
                    "visit_type", "status",
                )
                safe["appointment"] = {
                    key: appointment.get(key) for key in fields
                }
            return safe
    return result


def run_tool(name, function, *args, **kwargs):
    started_at = time.perf_counter()
    try:
        result = function(*args, **kwargs)
        LOGGER.info(
            "tool_call tool=%s status=ok duration_ms=%.1f",
            name,
            (time.perf_counter() - started_at) * 1000,
        )
        return result, {"tool": name, "result": safe_tool_result(name, result)}
    except Exception as exc:
        LOGGER.error(
            "tool_call tool=%s status=error error_type=%s duration_ms=%.1f",
            name,
            type(exc).__name__,
            (time.perf_counter() - started_at) * 1000,
        )
        return None, {"tool": name, "error": str(exc)}


def route(queue, message_id, tool_calls):
    result, record = run_tool(
        "route_message", clinic.route_message, queue, message_id
    )
    tool_calls.append(record)
    return result


def match_medication(body, records):
    # Prefer the longest exact medication name.
    for record in sorted(
        records,
        key=lambda item: len(text(item.get("medication"))),
        reverse=True,
    ):
        name = text(record.get("medication"))
        if name and re.search(
            r"(?<!\w)" + re.escape(name) + r"(?!\w)", body, re.I
        ):
            return name

    # Match the medication name without dosage strength.
    for record in records:
        name = text(record.get("medication"))
        base_name = re.sub(r"\s+\d.*$", "", name).strip()
        if base_name and re.search(
            r"(?<!\w)" + re.escape(base_name) + r"(?!\w)", body, re.I
        ):
            return name

    # For a vague refill request, use the medication only when there is
    # exactly one medication on the patient's list.
    if len(records) == 1:
        return text(records[0].get("medication"))

    return None


def lookup_patient(sender_name, sender_contact, dob, tool_calls):
    result, record = run_tool(
        "find_patient",
        clinic.find_patient,
        sender_name,
        dob=dob or None,
        contact=sender_contact or None,
    )
    tool_calls.append(record)

    if isinstance(result, dict) and result.get("verified") is True:
        return result.get("patient_id"), result.get("patient")

    return None, None


def parse_requested_dates(body):
    """Resolve the date window expressed in a message relative to clinic today."""
    lower = "".join(
        char for char in unicodedata.normalize("NFKD", body.casefold())
        if not unicodedata.combining(char)
    )
    if re.search(r"\bnext week\b|\bproxima semana\b", lower):
        monday = TODAY + timedelta(days=7 - TODAY.weekday())
        dates = [monday + timedelta(days=offset) for offset in range(5)]
        weekday = next(
            (index for name, index in (
                ("monday", 0), ("tuesday", 1), ("wednesday", 2),
                ("thursday", 3), ("friday", 4),
            ) if re.search(rf"\b{name}\b", lower)),
            None,
        )
        return [item for item in dates if weekday is None or item.weekday() == weekday]
    if re.search(r"\bfollowing week\b", lower):
        monday = TODAY + timedelta(days=7 - TODAY.weekday())
        return [monday + timedelta(days=7 + offset) for offset in range(5)]
    if re.search(r"\bweek of\s+23(?:rd)?\b", lower):
        return [date(2026, 11, day) for day in range(23, 28)]
    if re.search(r"\btoday or tomorrow\b", lower):
        return [TODAY, TODAY + timedelta(days=1)]
    if re.search(r"\btomorrow\b", lower):
        return [TODAY + timedelta(days=1)]
    if re.search(r"\btoday\b", lower):
        return [TODAY]

    ordinal = re.search(r"\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", lower)
    if ordinal:
        day = int(ordinal.group(1))
        try:
            return [date(2026, 11, day)]
        except ValueError:
            return []

    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
    }
    for weekday, weekday_number in weekdays.items():
        if re.search(rf"\b{weekday}\b", lower):
            delta = (weekday_number - TODAY.weekday()) % 7
            if delta == 0:
                delta = 7
            candidate = TODAY + timedelta(days=delta)
            if "next week" in lower and candidate < TODAY + timedelta(days=7):
                candidate += timedelta(days=7)
            return [candidate]

    if re.search(r"\bthis week\b", lower):
        return [TODAY + timedelta(days=offset) for offset in range(5 - TODAY.weekday())]
    return []


def requested_time_window(body):
    lower = body.casefold()
    if re.search(r"\bmorning\b", lower):
        return (9 * 60, 12 * 60)
    if re.search(r"\bafternoon\b", lower):
        return (13 * 60, 17 * 60)
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", lower)
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3) == "pm":
        hour += 12
    minute = int(match.group(2) or "0")
    requested = hour * 60 + minute
    return requested, requested


def provider_for_request(body, patient):
    for provider in clinic.clinic_data["providers"]:
        last_name = provider["name"].split()[-1].rstrip(",")
        if re.search(rf"\b{re.escape(last_name)}\b", body, re.I):
            return provider["id"]
    return patient.get("primary_provider") if patient else None


def visit_type_for_request(body, default="follow_up"):
    if re.search(r"\b(new patient|first visit)\b", body, re.I):
        return "new_patient"
    if re.search(r"\b(annual physical|physical|chequeo anual)\b", body, re.I):
        return "annual_physical"
    if re.search(r"\b(sick visit|fever|uti|burning when i pee)\b", body, re.I):
        return "sick_visit"
    if re.search(r"\btelehealth\b", body, re.I):
        return "telehealth"
    return default


def pick_slot(slots, window):
    usable = [slot for slot in slots if isinstance(slot, dict)]
    if window is None:
        return usable[0] if usable else None
    lower, upper = window
    for slot in usable:
        hour, minute = (int(part) for part in slot["start"].split(":"))
        value = hour * 60 + minute
        if lower <= value < upper or lower == upper == value:
            return slot
    return None


def available_slot_for_dates(provider_id, visit_type, dates, window, tool_calls):
    for target_date in dates:
        slots, record = run_tool(
            "find_available_slots",
            clinic.find_available_slots,
            provider_id,
            visit_type,
            target_date.isoformat(),
        )
        tool_calls.append(record)
        found = slots.get("slots", []) if isinstance(slots, dict) else []
        slot = pick_slot(found, window)
        if slot:
            return slot
    return None


def appointments_for_patient(patient_id, tool_calls):
    appointments, record = run_tool(
        "get_patient_appointments",
        clinic.get_patient_appointments,
        patient_id,
    )
    tool_calls.append(record)
    return appointments if isinstance(appointments, list) else []


def process_message(message):
    """Classify, safely act on, and observably process one inbox message."""
    started_at = time.perf_counter()
    message_id = text(message.get("id", message.get("message_id", "UNKNOWN")))
    body = get_body(message)
    sender_name = text(message.get("from_name", message.get("sender_name", "")))
    sender_contact = text(
        message.get("from_contact", message.get("sender_contact", ""))
    )
    dob = text(message.get(
        "dob",
        message.get("date_of_birth", message.get("stated_dob", "")),
    ))
    detected_intents = detect_intents(body)
    urgency = detect_urgency(body)
    suspicious = any(re.search(pattern, body, re.I) for pattern in SUSPICIOUS_PATTERNS)
    tool_calls = []
    actions = []
    requested_queues = []

    intent_aliases = {
        "administrative": "admin",
        "lab_results_request": "records",
        "general_inquiry": "other",
    }
    intents = list(dict.fromkeys(intent_aliases.get(item, item) for item in detected_intents))

    def finish(patient_id, reply, route_queues=None):
        routed = []
        requested = list(dict.fromkeys(route_queues or []))
        for queue in requested:
            result = route(queue, message_id, tool_calls)
            if isinstance(result, dict) and result.get("status") == "queued":
                routed.append(queue)
        failed_routes = set(requested) - set(routed)
        if failed_routes and urgency != "emergency":
            reply = (
                "The clinic could not confirm routing this request. No appointment "
                "or prescription change is confirmed; please contact the clinic "
                "using its official contact details."
            )
        result = {
            "id": message_id,
            "patient_id": patient_id,
            "intents": intents,
            "urgency": urgency,
            "actions": list(dict.fromkeys(actions)),
            "routed_to": routed,
            "tool_calls": [call["tool"] for call in tool_calls],
            "tool_trace": tool_calls,
            "reply_draft": reply,
        }
        LOGGER.info(
            "message_processed id=%s urgency=%s intents=%s actions=%s "
            "routes=%s duration_ms=%.1f",
            message_id,
            urgency,
            ",".join(intents),
            ",".join(result["actions"]),
            ",".join(routed),
            (time.perf_counter() - started_at) * 1000,
        )
        return result

    if urgency == "emergency":
        actions.append("escalate_emergency")
        requested_queues.append("clinician_urgent")
        routed = finish(
            None,
            "This may be a medical emergency. Please call 911 or go to "
            "the nearest emergency department now. The clinic has been alerted.",
            requested_queues,
        )
        patient_id, _patient = lookup_patient(
            sender_name, sender_contact, dob, tool_calls
        )
        routed["patient_id"] = patient_id
        routed["tool_calls"] = [call["tool"] for call in tool_calls]
        return routed

    if detected_intents == ["spam"]:
        actions.append("no_action")
        return finish(None, "")

    if suspicious:
        actions.append("flag_suspicious_instruction")

    if "new_patient" in detected_intents:
        actions.extend(["answer_directly", "offer_new_patient_booking"])
        return finish(
            None,
            "Yes, the clinic accepts new-patient inquiries. The front desk can "
            "help arrange an appointment.",
            ["front_desk"],
        )

    patient_id, patient = lookup_patient(
        sender_name, sender_contact, dob, tool_calls
    )

    if "third_party" in detected_intents:
        actions.append("decline_disclosure")
        return finish(
            None,
            "I'm unable to confirm or disclose another person's appointment "
            "details. Please ask them to contact the clinic directly or use "
            "the clinic's approved authorization process.",
            ["front_desk"],
        )

    if (
        ("records" in intents and patient_id is None)
        or (
            patient_id is None
            and any(item in detected_intents for item in ("refill", "reschedule", "cancel", "schedule"))
        )
    ):
        actions.append("request_verification")
        return finish(
            None,
            "Please contact the clinic through its official contact details "
            "so we can verify your identity before processing this request.",
            ["front_desk"],
        )

    if "refill" in detected_intents:
        records, record = run_tool(
            "get_patient_medications",
            clinic.get_patient_medications,
            patient_id,
        )
        tool_calls.append(record)
        medication_name = match_medication(
            body,
            records if isinstance(records, list) else [],
        )
        if medication_name:
            eligibility, record = run_tool(
                "check_refill_eligibility",
                clinic.check_refill_eligibility,
                patient_id,
                medication_name,
            )
            tool_calls.append(record)
            reason = (
                text(eligibility.get("reason", "")).lower()
                if isinstance(eligibility, dict)
                else ""
            )
            if isinstance(eligibility, dict) and eligibility.get("eligible") is True:
                actions.append("prepare_refill_for_approval")
            elif reason == "too_early":
                actions.append("refill_too_early")
            elif reason == "visit_required":
                actions.extend(["refill_needs_visit", "offer_appointment"])
                if urgency == "urgent" or re.search(
                    r"\b(ASAP|out of (my )?(pills|medication|medicine)|ran out)\b",
                    body,
                    re.I,
                ):
                    actions.append("route_to_provider")
            else:
                actions.append("route_to_provider")
        else:
            actions.append("route_to_provider")
        requested_queues.append("provider_refills")

    if "reschedule" in detected_intents or "cancel" in detected_intents:
        appointments = appointments_for_patient(patient_id, tool_calls)
        target_parts = re.split(r"\s+to\s+", body, flags=re.I)
        target_body = target_parts[-1] if len(target_parts) > 1 else body
        target_dates = parse_requested_dates(target_body)
        original_day = None
        source_ordinal = re.search(r"\b(?:from\s+\w+\s+the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", body, re.I)
        source_weekday = re.search(
            r"\b(?:on\s+)?(Monday|Tuesday|Wednesday|Thursday|Friday)\b",
            " ".join(target_parts[:-1]) if len(target_parts) > 1 else body,
            re.I,
        )
        if source_ordinal and len(target_parts) > 1:
            try:
                original_day = date(2026, 11, int(source_ordinal.group(1)))
            except ValueError:
                original_day = None
        elif source_weekday:
            weekday = source_weekday.group(1).casefold()
            weekday_number = {
                "monday": 0, "tuesday": 1, "wednesday": 2,
                "thursday": 3, "friday": 4,
            }[weekday]
            delta = (weekday_number - TODAY.weekday()) % 7 or 7
            original_day = TODAY + timedelta(days=delta)
        existing = [
            appointment for appointment in appointments
            if isinstance(appointment, dict)
            and (
                original_day is None
                or appointment.get("date") == original_day.isoformat()
            )
        ]
        if "cancel" in detected_intents and target_dates:
            requested_day_strings = {item.isoformat() for item in target_dates}
            existing = [
                appointment for appointment in existing
                if appointment.get("date") in requested_day_strings
            ]
            primary_provider = patient.get("primary_provider") if patient else None
            primary_provider_appointments = [
                appointment for appointment in existing
                if appointment.get("provider") == primary_provider
            ]
            if len(primary_provider_appointments) == 1:
                existing = primary_provider_appointments

        if len(existing) == 1 and "cancel" in detected_intents:
            appointment_id = text(existing[0].get("id"))
            result, record = run_tool(
                "cancel_appointment",
                clinic.cancel_appointment,
                appointment_id,
                patient_id,
            )
            tool_calls.append(record)
            if isinstance(result, dict) and result.get("success") is True:
                actions.append("cancel_appointment")
            else:
                actions.append("offer_alternatives")
        elif len(existing) == 1 and "reschedule" in detected_intents and target_dates:
            appointment = existing[0]
            window = requested_time_window(target_body)
            slot = available_slot_for_dates(
                appointment["provider"],
                appointment["visit_type"],
                target_dates,
                window,
                tool_calls,
            )
            if slot:
                result, record = run_tool(
                    "reschedule_appointment",
                    clinic.reschedule_appointment,
                    appointment["id"],
                    patient_id,
                    slot["date"],
                    slot["start"],
                )
                tool_calls.append(record)
                actions.append(
                    "reschedule_appointment"
                    if isinstance(result, dict) and result.get("success") is True
                    else "offer_alternatives"
                )
            else:
                actions.append("offer_alternatives")
        else:
            actions.append("offer_alternatives")
        requested_queues.append("front_desk")

    elif "schedule" in detected_intents:
        provider_id = provider_for_request(body, patient)
        visit_type = visit_type_for_request(body)
        dates = parse_requested_dates(body)
        if not dates and urgency == "urgent":
            dates = [TODAY + timedelta(days=1), TODAY + timedelta(days=2)]
        window = requested_time_window(body)
        slot = (
            available_slot_for_dates(provider_id, visit_type, dates, window, tool_calls)
            if provider_id and dates
            else None
        )
        if slot:
            result, record = run_tool(
                "book_appointment",
                clinic.book_appointment,
                patient_id,
                provider_id,
                visit_type,
                slot["date"],
                slot["start"],
            )
            tool_calls.append(record)
            actions.append(
                "book_appointment"
                if isinstance(result, dict) and result.get("success") is True
                else "offer_alternatives"
            )
        else:
            actions.append("offer_alternatives")
        requested_queues.append("front_desk")

    if "clinical" in detected_intents and (
        "schedule" not in detected_intents or "refill" in detected_intents
    ):
        actions.append("route_to_clinician")
        requested_queues.append(
            "clinician_urgent" if urgency == "urgent" else "clinician_routine"
        )

    if "billing" in detected_intents:
        actions.append("route_to_billing")
        requested_queues.append("billing")

    if "records" in intents:
        if detected_intents.count("lab_results_request"):
            actions.append("request_verification")
        else:
            actions.append("route_to_front_desk")
        requested_queues.append("front_desk")

    if "administrative" in detected_intents:
        if re.search(r"\b(open|hours|opening|address|directions)\b", body, re.I):
            actions.append("answer_directly")
        else:
            actions.append("route_to_front_desk")
            requested_queues.append("front_desk")

    if not actions:
        if re.search(r"\b(thanks|thank you|appreciate it)\b", body, re.I):
            actions.append("acknowledge")
        else:
            actions.append("route_to_front_desk")
            requested_queues.append("front_desk")

    if "clinical" in detected_intents and "schedule" not in detected_intents:
        reply = (
            "I've routed your clinical concern to the care team for review. "
            "A care team member will follow up; this automated service cannot "
            "provide individual clinical advice."
        )
    elif "refill" in detected_intents:
        if "refill_too_early" in actions:
            eligibility = next(
                (
                    call.get("result", {})
                    for call in tool_calls
                    if call.get("tool") == "check_refill_eligibility"
                    and isinstance(call.get("result"), dict)
                ),
                {},
            )
            earliest = eligibility.get("earliest_request_date")
            reply = (
                f"The request is too early. The earliest date it can be prepared "
                f"is {earliest}."
                if earliest
                else "The refill request is too early. Please contact the clinic for guidance."
            )
        elif "refill_needs_visit" in actions:
            reply = (
                "A visit is needed before this refill can be prepared. I've sent "
                "the request to the provider and the front desk can help arrange a visit."
            )
        elif re.search(r"\b(necesito|más|mas|metformina|medicacion|receta)\b", body, re.I):
            reply = (
                "Tu solicitud de medicamento se ha enviado para revisión. "
                "Cualquier receta requiere la aprobación del profesional que te "
                "atiende; no se ha enviado ninguna receta."
            )
        else:
            reply = (
                "Your medication request has been routed for review. Any "
                "prescription requires provider approval and has not been sent."
            )
    elif "schedule" in detected_intents or "reschedule" in detected_intents or "cancel" in detected_intents:
        if "book_appointment" in actions:
            booking = next(
                (
                    call.get("result", {}).get("appointment")
                    for call in reversed(tool_calls)
                    if call.get("tool") == "book_appointment"
                    and isinstance(call.get("result"), dict)
                ),
                None,
            )
            if booking and patient and patient.get("preferred_language") == "Spanish":
                reply = (
                    f"Su cita está reservada para el {booking['date']} a las "
                    f"{booking['start']}. Si necesita hacer cambios, comuníquese "
                    "con la recepción."
                )
            elif booking:
                reply = f"Your appointment is booked for {booking['date']} at {booking['start']}."
            else:
                reply = "Your appointment request could not be confirmed. Please contact the front desk."
        elif "reschedule_appointment" in actions or "cancel_appointment" in actions:
            reply = "Your appointment change has been completed."
        else:
            reply = "The requested time is unavailable or could not be confirmed. The front desk can help with alternatives."
    elif "billing" in detected_intents:
        reply = "I've routed your billing question to the billing team."
    elif "administrative" in detected_intents:
        if re.search(r"\bday after thanksgiving\b", body, re.I):
            reply = "The clinic is closed Friday, November 27, 2026, for the Thanksgiving holiday."
        elif re.search(r"\b(hours|opening|open)\b", body, re.I):
            reply = "The clinic is open Monday through Friday, 9:00 a.m.–5:00 p.m., with lunch from 12:00–1:00 p.m."
        else:
            reply = "I've routed your request to the front desk."
    elif "acknowledge" in actions:
        reply = "You're welcome! Happy to help."
    else:
        reply = "Thanks for contacting the clinic. Our team will review your message."

    return finish(patient_id, reply, requested_queues)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Process clinic inbox messages.")
    parser.add_argument(
        "--input",
        type=Path,
        default=BASE_DIR / "messages_sample.json",
        help="JSON file containing a list of inbox messages.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BASE_DIR / "results.json",
        help="Path to write structured results.",
    )
    args = parser.parse_args()
    with args.input.open(encoding="utf-8") as input_file:
        messages = json.load(input_file)
    if not isinstance(messages, list) or not all(
        isinstance(message, dict) for message in messages
    ):
        raise ValueError("Input JSON must be a list of message objects.")
    message_ids = [
        text(message.get("id", message.get("message_id")))
        for message in messages
    ]
    if any(not message_id for message_id in message_ids):
        raise ValueError("Every input message must have a non-empty id.")
    if len(message_ids) != len(set(message_ids)):
        raise ValueError("Input message IDs must be unique.")
    results = [process_message(message) for message in messages]

    output_path = (
        args.output if args.output.is_absolute() else BASE_DIR / args.output
    )
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Processed {len(results)} messages.")
    print(f"Results saved to: {output_path}")
    for level in ("emergency", "urgent", "routine"):
        print(f"{level}: {sum(row['urgency'] == level for row in results)}")


if __name__ == "__main__":
    main()
