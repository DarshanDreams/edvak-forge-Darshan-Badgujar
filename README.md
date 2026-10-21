# EDVAK Forge — AI Front-Desk Agent

A deterministic, safety-first front-desk agent for the fictional Maplewood
Family Clinic. It processes synthetic inbox messages, calls clinic tools
through the Model Context Protocol (MCP), routes messages to human queues,
and records a structured result for every message.

> **Safety:** This is a take-home prototype using synthetic data only. It is
> not a clinical system, must not be used for care decisions, and must never
> be given real patient information.

## Evaluation

Run `python agent.py` followed by `python evaluate.py` to reproduce these
results on the supplied labeled sample:

| Metric | Result |
| --- | ---: |
| Messages processed | 40 / 40 |
| Urgency accuracy | 40 / 40 (100%) |
| Strict emergency recall (unambiguous emergency labels) | 4 / 4 (100%) |
| Exact action match | 40 / 40 (100%) |
| Action precision / recall | 100% / 100% |
| Exact intent match | 40 / 40 (100%) |
| Output, tool, and safety invariant errors | 0 |
| Labeled must-not violations | 0 |

Two labels accept either `urgent` or `emergency`. The agent classifies
passive hopelessness (M006) as urgent and the explicit bleeding emergency
(M017) as emergency. The evaluator checks the accepted urgency for each
message and reports strict emergency recall separately.

These are results on a small, fixed sample, not estimates of real-world
accuracy or proof of safety on unseen messages. The evaluator reports
individual mismatches, emergency misses, tool failures, malformed output,
unsafe emergency responses, appointment conflicts, and must-not violations;
it exits nonzero if any are found.

## Architecture

1. **JSON data:** `clinic.json`, `patients.json`, `medications.json`, and
   `appointments.json` are the synthetic clinic policy and records.
   Operational changes live in memory for the process run.
2. **Clinic tools (`tools.py`):** patient verification, caregiver
   authorization, refill eligibility, appointment lookup and scheduling,
   and queue validation.
3. **MCP server (`server.py`):** exposes clinic operations as MCP tools over
   stdio.
4. **MCP client bridge (`mcp_bridge.py`):** maintains the MCP client session,
   validates response types, and closes the server cleanly.
5. **Agent (`agent.py`):** deterministic intent and urgency classification,
   safety and privacy checks, tool orchestration, queue routing, and reply
   drafting. Emergency messages are escalated before routine processing.
   Appointment operations are only reported as complete after a successful
   tool response. Refills are never approved or prescribed by the agent.
6. **Evaluation and tests:** `evaluate.py` checks labels, output shape, tool
   evidence, routing, emergency response, privacy, and appointment
   constraints. `tests/test_project.py` covers key policy and integration
   behaviors.

The agent is deterministic rather than LLM-driven to make decisions
reproducible and auditable on this assignment. No LLM provider is used; the
sample run's model inference cost is **$0**. This excludes development and
local compute.

## What was reused and built

- **Reused:** Python standard library, the MCP Python SDK, and the supplied
  synthetic JSON data and labels.
- **Built:** clinic operations, MCP server and bridge, classification and
  safety logic, evaluation harness, regression tests, and run instructions.
- **Storage:** JSON files are used instead of MongoDB; run-scoped in-memory
  state is sufficient for this prototype.

## Requirements

- Python 3.12
- Dependencies in `requirements.txt`

## Setup and run

From the project directory in Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m py_compile .\tools.py .\server.py .\mcp_bridge.py .\agent.py .\evaluate.py
python agent.py
python evaluate.py
python -m unittest discover -s tests -v
```

The MCP server is started automatically over stdio by the client bridge.
`agent.py` writes `results.json`; `evaluate.py` prints the metrics and
diagnostics.

To process a new batch, supply a JSON list of message objects:

```powershell
python agent.py --input .\new_messages.json --output .\new_results.json
```

## Output

Each result includes the assignment fields: `id`, verified `patient_id`
(or `null`), `intents`, `urgency`, `actions`, list-valued `routed_to`,
`tool_calls` (tool names), and `reply_draft`. An additional sanitized
`tool_trace` records tool outcomes and errors without including patient
demographics or medication names. The reply is a draft and is not sent.

## Limitations and next steps

The rule-based classifier may miss paraphrases, typos, multilingual
messages, ambiguous identity, and combinations absent from the sample. A
perfect score on these examples can reflect adaptation to the supplied
cases; it does not establish performance on unseen messages.

With another week, I would add an independent unseen/adversarial evaluation
set, expand randomized boundary and integration tests, and add operational
dashboards and alerts for tool availability, routing latency, and escalation
delivery. I would also investigate versioned policy rules and safe
idempotency for state-changing operations.

## Before any real-patient use

This prototype is **not suitable for real patient data**. Before deployment,
it would require formal privacy, security, legal, and clinical-safety review;
approved identity and caregiver authorization; least-privilege access;
encryption and secrets management; retention controls; transactional storage
and idempotency; clinician-approved escalation protocols; human oversight;
monitoring and incident response; and compliance with applicable healthcare
requirements. No real patient data is included or used.