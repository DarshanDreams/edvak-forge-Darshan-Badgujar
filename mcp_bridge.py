"""Synchronous bridge to the persistent EDVAK Forge MCP stdio server.

The agent remains synchronous for deterministic evaluation, while all clinic
operations are executed through the official MCP ClientSession.
"""
from __future__ import annotations

import asyncio
import atexit
import json
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import tools as local_data

BASE_DIR = Path(__file__).resolve().parent


class MCPBridge:
    """Keep one MCP server process/session alive and expose sync tool calls."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._startup_error: BaseException | None = None
        self._session: ClientSession | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._serve_future = asyncio.run_coroutine_threadsafe(
            self._serve(), self._loop
        )
        if not self._ready.wait(timeout=20):
            self.close()
            raise RuntimeError("Timed out starting the EDVAK MCP server.")
        if self._startup_error is not None:
            self.close()
            raise RuntimeError(
                "Could not start the EDVAK MCP server. Check server.py and MCP dependencies."
            ) from self._startup_error

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        self._loop.close()

    async def _serve(self) -> None:
        from contextlib import AsyncExitStack

        self._shutdown_event = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                params = StdioServerParameters(
                    command=sys.executable,
                    args=[str(BASE_DIR / "server.py")],
                )
                read_stream, write_stream = await stack.enter_async_context(
                    stdio_client(params)
                )
                self._session = await stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await self._session.initialize()
                self._ready.set()
                await self._shutdown_event.wait()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            raise

    async def _call_async(self, name: str, arguments: dict[str, Any]) -> Any:
        if self._session is None:
            raise RuntimeError("MCP session is not initialized")
        result = await self._session.call_tool(name, arguments=arguments)
        if getattr(result, "isError", False):
            messages = [getattr(item, "text", "") for item in result.content]
            raise RuntimeError(f"MCP tool {name} failed: {' '.join(messages)}")
        # MCP SDK versions differ in how JSON returned by a tool is exposed.
        # A tool returning json.dumps(...) may arrive as text, a JSON-encoded
        # string, or structuredContent wrapping the returned string.
        def decode_payload(value: Any) -> Any:
            if isinstance(value, dict) and set(value) == {"result"}:
                value = value["result"]
            for _ in range(2):
                if not isinstance(value, str):
                    break
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    break
            return value

        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return decode_payload(structured)

        for item in result.content:
            content_text = getattr(item, "text", None)
            if content_text:
                return decode_payload(content_text)
        return None

    def close(self) -> None:
        if not self._loop.is_running():
            return
        try:
            if not self._serve_future.done() and self._shutdown_event is not None:
                self._loop.call_soon_threadsafe(self._shutdown_event.set)
                self._serve_future.result(timeout=10)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread.is_alive():
                self._thread.join(timeout=5)

    def call(self, tool_name: str, **arguments: Any) -> Any:
        if self._startup_error:
            raise RuntimeError("MCP server startup failed") from self._startup_error
        future: Future = asyncio.run_coroutine_threadsafe(
            self._call_async(tool_name, arguments), self._loop
        )
        return future.result(timeout=30)

    @staticmethod
    def _require_dict(tool_name: str, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise TypeError(f"MCP tool {tool_name} returned an invalid result")
        return result

    # The following methods mirror the signatures used by agent.py.
    def find_patient(self, name: str, dob: str | None = None,
                     contact: str | None = None) -> dict[str, Any]:
        result = self.call(
            "find_patient", name=name, dob=dob or "", contact=contact or ""
        )
        return self._require_dict("find_patient", result)

    def get_patient_medications(self, patient_id: str) -> list[dict[str, Any]]:
        result = self.call("get_patient_medications", patient_id=patient_id)
        if not isinstance(result, list):
            raise TypeError("MCP tool get_patient_medications returned an invalid result")
        return result

    def get_patient_appointments(self, patient_id: str) -> list[dict[str, Any]]:
        result = self.call("get_patient_appointments", patient_id=patient_id)
        if not isinstance(result, list):
            raise TypeError("MCP tool get_patient_appointments returned an invalid result")
        return result

    def check_refill_eligibility(self, patient_id: str,
                                 medication_name: str) -> dict[str, Any]:
        result = self.call(
            "check_refill_eligibility",
            patient_id=patient_id,
            medication_name=medication_name,
        )
        return self._require_dict("check_refill_eligibility", result)

    def find_available_slots(self, provider_id: str, visit_type: str,
                             target_date: str) -> dict[str, Any]:
        result = self.call(
            "find_available_slots",
            provider_id=provider_id,
            visit_type=visit_type,
            target_date=target_date,
        )
        return self._require_dict("find_available_slots", result)

    def book_appointment(self, patient_id: str, provider_id: str,
                         visit_type: str, target_date: str,
                         start_time: str) -> dict[str, Any]:
        result = self.call(
            "book_appointment",
            patient_id=patient_id,
            provider_id=provider_id,
            visit_type=visit_type,
            target_date=target_date,
            start_time=start_time,
        )
        return self._require_dict("book_appointment", result)

    def reschedule_appointment(self, appointment_id: str, patient_id: str,
                               target_date: str,
                               start_time: str) -> dict[str, Any]:
        result = self.call(
            "reschedule_appointment",
            appointment_id=appointment_id,
            patient_id=patient_id,
            target_date=target_date,
            start_time=start_time,
        )
        return self._require_dict("reschedule_appointment", result)

    def cancel_appointment(self, appointment_id: str,
                           patient_id: str) -> dict[str, Any]:
        result = self.call(
            "cancel_appointment",
            appointment_id=appointment_id,
            patient_id=patient_id,
        )
        return self._require_dict("cancel_appointment", result)

    def route_message(self, queue: str, message_id: str) -> dict[str, Any]:
        result = self.call(
            "route_message",
            queue=queue,
            message_id=message_id,
        )
        return self._require_dict("route_message", result)

    def __getattr__(self, name: str) -> Any:
        # Read-only synthetic data is used for caregiver matching and medication
        # name matching; operational actions above always go through MCP.
        return getattr(local_data, name)


clinic = MCPBridge()
atexit.register(clinic.close)

# Clinic metadata is read-only; patient operations use the MCP server.
clinic_data = local_data.clinic

# Expose methods directly to preserve agent.py's existing call style.
find_patient = clinic.find_patient
get_patient_medications = clinic.get_patient_medications
get_patient_appointments = clinic.get_patient_appointments
check_refill_eligibility = clinic.check_refill_eligibility
find_available_slots = clinic.find_available_slots
book_appointment = clinic.book_appointment
reschedule_appointment = clinic.reschedule_appointment
cancel_appointment = clinic.cancel_appointment
route_message = clinic.route_message
