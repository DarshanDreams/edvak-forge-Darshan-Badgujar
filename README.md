# EDVAK Forge --- AI Front-Desk Agent

A deterministic, safety-first front-desk agent for the fictional
Maplewood Family Clinic. It processes the 40 supplied synthetic
messages, classifies urgency and intent, uses clinic tools exposed
through the Model Context Protocol (MCP), and records a structured
result for each message.

> **Safety note:** This project uses synthetic data only. It is a
> take-home prototype, not a clinical system, and must not be used to
> make care decisions or handle real patient information.

## Evaluation results

Evaluation on the supplied 40-message sample set (run with
`python agent.py` followed by `python evaluate.py`):

| Metric | Result |
| --- | ---: |
| Messages processed | 40 / 40 |
| Urgency accuracy | 40 / 40 (100%) |
| Strict emergency recall (labels accepting only emergency) | 4 / 4 (100%) |
| Exact action match | 40 / 40 (100%) |
| Action precision / recall | 100% / 100% |
| Exact intent match | 40 / 40 (100%) |
| Output, tool, and safety invariant errors | 0 |
| Must-not violations | 0 |

These figures describe performance on the supplied labelled sample only.
They do not establish performance on unseen messages or production
readiness. Two labels permit either `urgent` or `emergency`; the agent
chooses `urgent` for passive hopelessness (M006) and `emergency` for the
explicit bleeding emergency (M017). The evaluator reports strict
emergency recall separately and also checks accepted urgency per case.
The sample is small and must not be treated as a general accuracy estimate.

## Architecture

The implementation separates deterministic clinic operations from
message handling:

1.  **JSON data layer** --- `clinic.json`, `patients.json`,
    `medications.json`, and `appointments.json` provide the synthetic
    clinic policies and records. State-changing operations are kept in
    process for a run, as permitted by the assignment.
2.  **Clinic tools** --- `tools.py` implements patient
    lookup/verification, refill-policy checks, appointment availability
    and operations, and message routing.
3.  **MCP server** --- `server.py` exposes the clinic operations as MCP
    tools over stdio. It provides tools for patient verification,
    medication and appointment lookup, refill eligibility, slot search,
    booking, rescheduling, cancellation, and routing.
4.  **MCP client bridge** --- `mcp_bridge.py` connects the agent to the
    local MCP server, validates responses, and shuts down its stdio session.
5.  **Agent** --- `agent.py` detects intent and urgency, applies
    explicit safety rules, calls tools where appropriate, chooses human
    queues, and drafts a reply. Emergency escalation precedes identity
    lookup; normal refill and appointment changes require verification.
    Appointment booking, cancellation, and rescheduling call the actual
    stateful tools and confirm success before claiming completion.
6.  **Evaluation harness** --- `evaluate.py` compares `results.json`
    against `labels_sample.json` and validates the output contract, action
    vocabulary, route/tool consistency, verified patient IDs, safe emergency
    responses, state-changing tool evidence, appointment constraints,
    urgency, intent/action metrics, and every labelled `must_not` constraint.

Processing logs include message IDs, intents, actions, queue outcomes,
tool status, and durations. They deliberately do not log message bodies.
The result's `tool_trace` is also sanitized: it omits demographic details,
medication names, and patient IDs from appointment records.

The agent is deliberately deterministic rather than LLM-driven. For this
small labelled dataset, explicit rules make behaviour reproducible and
easier to audit. No LLM provider is used, so model inference cost for
the current run is **\$0**. This does not include development time or
local compute.

## What was reused and what was built

-   **Reused:** Python standard-library capabilities, the MCP Python
    SDK, and the supplied synthetic JSON dataset and labelled evaluation
    cases.
-   **Built/adapted for this project:** the deterministic clinic
    operations in `tools.py`, MCP wrappers in `server.py`, stdio client
    bridge in `mcp_bridge.py`, intent/urgency and safety logic in
    `agent.py`, and the evaluation/reporting workflow in `evaluate.py`.
-   **Storage choice:** the supplied JSON files are used instead of
    MongoDB to keep the prototype small and reproducible. In-memory
    changes are sufficient for the assignment's run-scoped persistence
    requirement.

## Requirements

-   Python 3.12 (the project was tested with Python 3.12)
-   Dependencies from `requirements.txt`, including the MCP Python SDK

## Setup and run

Run these commands from the project directory.

### Windows PowerShell

``` powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m py_compile .\tools.py .\server.py .\mcp_bridge.py .\agent.py .\evaluate.py
python agent.py
python evaluate.py
```

If PowerShell blocks virtual-environment activation, use the Python
executable inside `.venv` directly or follow your local PowerShell
execution-policy guidance.

`agent.py` writes one output row per input message to `results.json`.
`evaluate.py` prints the metrics and mismatch/violation details. The MCP
server is started by the bridge over stdio; it does not need to be
launched manually in a separate terminal for the normal agent run.
For a new batch, provide a JSON list of message objects and an output path:

``` powershell
python agent.py --input .\new_messages.json --output .\new_results.json
```

Run the focused policy and end-to-end regression tests:

``` powershell
python -m unittest discover -s tests -v
```

## Output format

Each row in `results.json` contains:

-   `id` --- input message ID
-   `patient_id` --- verified patient ID, or `null` when verification is
    not established
-   `intents` --- detected message intents
-   `urgency` --- `emergency`, `urgent`, or `routine`
-   `actions` --- selected action labels
-   `routed_to` --- list of destination queues
-   `tool_calls` --- list of tool names called
-   `tool_trace` --- tool names with results or explicit errors for audit
-   `reply_draft` --- a proposed response, not a message sent to a
    patient

The agent does not issue prescriptions. Refill outcomes are
recommendations for review under the supplied clinic policy, and
clinical issues are routed to a human team.

## Known limitations and what I would improve with another week

The 40-message evaluation is small and fixed. The deterministic
classification rules are not a substitute for clinical review and may
miss paraphrases, typos, multilingual messages, ambiguous identity
details, and combinations not represented in the sample. A perfect score
on these cases may reflect adaptation to the supplied examples; it is not
evidence of general accuracy or safety.

With another week, I would:

1.  Add a separate, unseen test set with paraphrases, adversarial
    messages, edge cases, and multi-intent combinations.
2.  Expand the included tool and end-to-end tests into randomized and
    adversarial suites, especially for verification, caregiver authority,
    booking conflicts, provider leave, holidays, and refill boundaries.
3.  Add durable dashboards and alerts for tool availability, routing
    latency, escalation delivery, and safely redacted operational metrics.
4.  Make policy rules and action selection easier to inspect through
    explicit decision records and versioned policy tests.
5.  Add robust handling for tool timeouts, malformed tool responses,
    retries where safe, and idempotency for state-changing operations.
6.  Compare the deterministic baseline with an LLM-assisted classifier
    only if it can be constrained, evaluated, and safely fall back to
    deterministic rules and human review.

## Requirements before using real patient data

This prototype is not suitable for real patient data. Before any real
deployment, I would require at least:

-   A formal privacy, security, clinical-safety, and legal review.
-   Approved identity verification and caregiver-authorization flows;
    least-privilege access and role-based authorization.
-   Encryption in transit and at rest, secrets management,
    retention/deletion controls, and auditable access logs.
-   A secure, transactional data store with concurrency control and
    idempotency for appointment changes.
-   Explicit clinician-approved escalation protocols, reliable emergency
    handling, human oversight, and a safe failure mode when tools are
    unavailable.
-   Monitoring, incident response, access reviews, backup/recovery
    procedures, and validated testing against representative cases.
-   Appropriate consent, data-processing agreements, and compliance with
    applicable healthcare and privacy requirements.
-   No use of patient data for model training or external services
    without explicit authorization and review.

## Project files

``` text
edvak-forge/
├── agent.py
├── mcp_bridge.py
├── server.py
├── tools.py
├── evaluate.py
├── clinic.json
├── patients.json
├── medications.json
├── appointments.json
├── messages_sample.json
├── labels_sample.json
├── requirements.txt
├── tests/
│   └── test_project.py
├── results.json
└── README.md
```

The project is a local take-home prototype. Keep `.venv/`, Python cache
directories, and temporary backup files out of the Git repository.
