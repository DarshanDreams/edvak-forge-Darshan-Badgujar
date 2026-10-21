
"""MCP server exposing the fictional clinic's deterministic tools."""

import json
from mcp.server.fastmcp import FastMCP

import tools as clinic_tools

mcp = FastMCP("edvak-forge")


def serialize(result):
    """Return tool results as JSON for consistent MCP client decoding."""
    return json.dumps(result, default=str)


@mcp.tool()
def find_patient(name: str, dob: str = "", contact: str = "") -> str:
    """Find and verify a patient using the clinic's verification policy."""
    result = clinic_tools.find_patient(
        name=name,
        dob=dob or None,
        contact=contact or None,
    )
    return serialize(result)


@mcp.tool()
def get_patient_medications(patient_id: str) -> str:
    """Retrieve medication records for a patient ID."""
    patient_records = [
        record
        for record in clinic_tools.medications
        if isinstance(record, dict)
        and str(record.get("patient_id", "")) == str(patient_id)
    ]
    return serialize(patient_records)


@mcp.tool()
def get_patient_appointments(patient_id: str) -> str:
    """List a patient's currently booked appointments."""
    return serialize(clinic_tools.get_patient_appointments(patient_id))


@mcp.tool()
def check_refill_eligibility(
    patient_id: str,
    medication_name: str,
) -> str:
    """Check refill eligibility under clinic policy; never issue prescriptions."""
    result = clinic_tools.check_refill_eligibility(
        patient_id,
        medication_name,
    )
    return serialize(result)


@mcp.tool()
def find_available_slots(
    provider_id: str,
    visit_type: str,
    target_date: str,
) -> str:
    """Find available appointment slots on a specified date."""
    result = clinic_tools.find_available_slots(
        provider_id,
        visit_type,
        target_date,
    )
    return serialize(result)


@mcp.tool()
def book_appointment(
    patient_id: str,
    provider_id: str,
    visit_type: str,
    target_date: str,
    start_time: str,
) -> str:
    """Book an appointment through the clinic's scheduling policy."""
    result = clinic_tools.book_appointment(
        patient_id,
        provider_id,
        visit_type,
        target_date,
        start_time,
    )
    return serialize(result)


@mcp.tool()
def reschedule_appointment(
    appointment_id: str,
    patient_id: str,
    target_date: str,
    start_time: str,
) -> str:
    """Reschedule an existing appointment."""
    result = clinic_tools.reschedule_appointment(
        appointment_id,
        patient_id,
        target_date,
        start_time,
    )
    return serialize(result)


@mcp.tool()
def cancel_appointment(appointment_id: str, patient_id: str) -> str:
    """Cancel an existing appointment."""
    result = clinic_tools.cancel_appointment(appointment_id, patient_id)
    return serialize(result)


@mcp.tool()
def route_message(queue: str, message_id: str) -> str:
    """Route a message to an allowed human queue."""
    result = clinic_tools.route_message(queue, message_id)
    return serialize(result)


if __name__ == "__main__":
    mcp.run(transport="stdio")
