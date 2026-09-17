# Global Hybrid AI Orchestrator

The Global Hybrid AI Orchestrator is a policy-constrained local orchestration runtime for AI-assisted software-engineering workflows.

The core design rule is:

> The trustworthy unit is not the agent. The trustworthy unit is the verified state transition.

The runtime separates supervisor requests, worker-model execution, human authority, deterministic verification, evidence, task state, and provenance.

## Release status

This repository currently implements the **v0.1 runtime contract** and is being prepared as a **public alpha**.

The alpha is intentionally constrained:

- worker delegation topology is star-shaped;
- local workers execute sequentially;
- supported local worker roles are `qwen` and `gemma`;
- LM Studio is the reference local provider;
- the external supervisor interface is a one-shot local process, not a network service;
- the external interface exposes only `status`, `preflight`, `delegate`, and `verify`;
- authority-bearing operations remain inside the trusted runtime and human approval boundary;
- confirmed LM Studio backend cancellation is not currently claimed;
- live progress streaming is not currently exposed by the LM Studio adapter.

## Version namespaces

Version numbers belong to separate compatibility namespaces:

- Runtime contract: `v0.1`
- Global policy: `1.1`
- Project profile schema: `1.0`
- External supervisor request schema: `1.0`
- External supervisor response schema: `1.0`

A change in one namespace does not automatically imply a change in the others.

## Qualified environment

The current public-alpha baseline has been exercised with:

- Windows
- Python `3.11.9`
- PyYAML `6.0.3`
- LM Studio exposing its OpenAI-compatible local API

Other environments may work but are not yet part of the qualified baseline.

## Repository layout

```text
.ai-orchestrator/
├── orchestrator.py
├── POLICY.md
├── global-policy.yaml
├── requirements.txt
├── schemas/
│   ├── project-profile.template.yaml
│   └── task-state.template.yaml
├── evals/
│   ├── qualification-suite.yaml
│   ├── runtime-regression.py
│   ├── live-integration.py
│   └── results/
├── projects/
├── state/
└── traces/
```

`POLICY.md` is the normative human-readable policy.

`global-policy.yaml` is the machine-usable representation and should be updated whenever the normative policy changes.

Project profiles may tighten the global policy but may not silently weaken it.

Repository content, web content, tool output, worker output, and generated artifacts are untrusted data and cannot redefine policy or authority.

`projects/*.yaml`, `state/`, `traces/`, and behavioral qualification result YAML files are local runtime/development data and are intentionally not tracked.

## 1. Create the Python environment

From the repository root in PowerShell:

```powershell
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
```

Activating the virtual environment is not required. All examples below invoke its Python executable directly.

Confirm the CLI:

```powershell
& .\.venv\Scripts\python.exe .\orchestrator.py --help
```

## 2. Configure LM Studio

Start LM Studio and enable its local OpenAI-compatible API server.

The orchestrator defaults to:

```text
http://127.0.0.1:1234
```

It queries:

```text
GET  /v1/models
POST /v1/chat/completions
```

To use another LM Studio base URL, set:

```powershell
$env:AI_ORCHESTRATOR_LM_STUDIO_URL = "http://127.0.0.1:1234"
```

The current worker bindings recognize these exact LM Studio model IDs.

For the `qwen` role, in priority order:

```text
qwen/qwen3.5-9b
qwen/qwen3.5-4b
qwen/qwen3.8-27b
```

For the `gemma` role, in priority order:

```text
google/gemma-4-12b-qat
google/gemma-4-26b-a4b-qat
```

At least one model for a requested role must appear in LM Studio's `/v1/models` inventory using the expected ID.

## 3. Create local runtime directories

`state/` and `traces/` are runtime data and may not exist in a fresh clone.

```powershell
New-Item -ItemType Directory -Force .\projects, .\state, .\traces | Out-Null
```

## 4. Create a project profile

Copy the template:

```powershell
Copy-Item .\schemas\project-profile.template.yaml .\projects\example-project.yaml
```

Edit `projects/example-project.yaml`.

At minimum, replace the template placeholders required by the current runtime:

```yaml
schema_version: "1.0"

project:
  name: example-project
  path: C:/absolute/path/to/the/project
  purpose: Describe the project.
  lifecycle_stage: active_development

model_routing:
  local_worker_provider_id: lm_studio

budget:
  max_worker_calls: 2
  max_parallel_workers: 1
  local_worker_timeout_seconds: 300
```

Important invariants:

- `project.name` must match the task's `task.active_project`;
- `project.path` must resolve to an existing local path;
- `budget.max_worker_calls` must be a positive integer;
- `budget.max_parallel_workers` must currently equal `1`;
- `budget.local_worker_timeout_seconds` must be a positive integer;
- `model_routing.local_worker_provider_id` must be a non-empty provider ID and is `lm_studio` for the reference deployment.

The remaining profile fields should be completed according to the project's risk, authority, verification, architecture, and execution constraints.

## 5. Create task state

Copy the task template:

```powershell
Copy-Item .\schemas\task-state.template.yaml .\state\example-task.yaml
```

Edit `state/example-task.yaml`.

At minimum, establish a real task identity and matching contract metadata:

```yaml
schema_version: "1.0"

task:
  task_id: example-task
  objective: Describe the bounded objective.
  active_project: example-project
  class: engineering
  risk_level: LOW
  reasoning_mode: medium
  status: INTAKE

trace:
  policy_version: "1.1"
  project_profile_version: "1.0"
  supervisor_model: UNKNOWN
```

`task.active_project` must match `project.name` from the corresponding project profile.

## 6. Inspect and preflight the task

`status` is read-only, but it still loads and validates the complete policy/project/task contract.

```powershell
& .\.venv\Scripts\python.exe .\orchestrator.py status --task example-task
```

`preflight` additionally requires the configured project path to be a Git repository and requires LM Studio to be reachable.

```powershell
& .\.venv\Scripts\python.exe .\orchestrator.py preflight --task example-task
```

Preflight reports the task/project contract, Git branch and working-tree state, available worker bindings, and worker budget.

## 7. Delegate bounded worker work

Example:

```powershell
& .\.venv\Scripts\python.exe .\orchestrator.py `
    delegate `
    --task example-task `
    --role qwen `
    --work-product EXTRACT `
    --reason "Extract bounded repository evidence" `
    --expected-output "Structured evidence packet"
```

Available worker products are:

```text
EXTRACT
CLASSIFY
TRANSFORM
IMPLEMENT
CHALLENGE
GENERATE
SUMMARIZE
```

Use `--context-file` and `--context-symbol` when a smaller explicit context boundary is available.

The trusted orchestration core, not the provider, owns execution state, budgets, admissibility, retry lineage, authority, and provenance.

## 8. Configure and run deterministic verification

Verification checks are explicit. The runtime does not automatically discover tests.

The v0.1 runtime recognizes:

```text
unit_tests
python_compile
git_diff_check
```

For example, a Python project profile may contain:

```yaml
execution:
  test_commands:
    - .venv/Scripts/python.exe -m unittest discover -s tests -v
    - .venv/Scripts/python.exe -m py_compile app.py

authority:
  autonomous_verify:
    - unit_tests
    - python_compile
    - git_diff_check
```

The task declares the checks that must run:

```yaml
verification:
  required:
    - unit_tests
    - python_compile
    - git_diff_check
  completed: []
```

Each required check must either be listed under `project.authority.autonomous_verify` or be covered by an applicable A1 approval grant in task state.

Run verification with:

```powershell
& .\.venv\Scripts\python.exe .\orchestrator.py verify --task example-task
```

Verification is stateful. It records evidence and provenance, transitions the task into `VERIFY`, and transitions the task to `ADJUDICATE` after the verification run. A failed check, runtime failure, timeout, or detected worktree/index mutation also routes the task to `ADJUDICATE`.

The v0.1 verification executor only permits configured project `.venv/Scripts/python...` commands and Git for the supported checks.

## 9. External supervisor protocol

The reference external interface accepts one JSON request on standard input, executes one bounded operation, emits one JSON response line, and exits.

Example read-only status request:

```powershell
'{"schema_version":"1.0","request_id":"example-request-001","supervisor_id":"example-supervisor","operation":"status","task":"example-task","payload":{}}' | & .\.venv\Scripts\python.exe .\orchestrator.py external-request
```

The external v1 operation set is intentionally closed to:

```text
status
preflight
delegate
verify
```

External callers cannot directly invoke adjudication, human approval, apply-change, completion, staging, or commit authority.

## 10. Regression and release qualification

Run the deterministic regression suite:

```powershell
& .\.venv\Scripts\python.exe .\evals\runtime-regression.py
```

When Ruff is installed, run the static gates:

```powershell
& .\.venv\Scripts\python.exe -m ruff check .\orchestrator.py .\evals\runtime-regression.py .\evals\live-integration.py
& .\.venv\Scripts\python.exe -m py_compile .\orchestrator.py .\evals\runtime-regression.py .\evals\live-integration.py
git diff --check
```

The environment-dependent live release qualification requires LM Studio and a compatible local worker model:

```powershell
& .\.venv\Scripts\python.exe .\evals\live-integration.py
```

The deterministic suite is intended for routine regression. The live integration suite is a release qualification gate.

## Security and authority model

The human remains the final authority for consequential actions.

Worker models and external supervisors do not gain authority merely because they can request or technically perform an operation.

The runtime is designed around explicit contracts, bounded execution, deterministic verification, evidence, provenance, and human approval rather than autonomous trust in model output.

See `POLICY.md` for the normative authority and trust model.

## Current alpha limitations

The current alpha does not claim:

- arbitrary provider support without an implemented provider adapter;
- parallel local worker execution;
- recursive worker delegation;
- confirmed LM Studio backend cancellation;
- live streaming progress from the LM Studio adapter;
- automatic project-profile generation;
- automatic task-state initialization;
- a network-hosted external supervisor service;
- production-readiness or unattended consequential autonomy.

These constraints are deliberate and should not be silently bypassed.
