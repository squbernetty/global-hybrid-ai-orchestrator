from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import orchestrator


class IntegrationFailure(RuntimeError):
    pass


def check(label: str, condition: bool) -> None:
    if not condition:
        raise IntegrationFailure(label)

    print(f"PASS: {label}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_live_lm_studio_provider() -> None:
    print("\n=== N11 LIVE LM STUDIO PROVIDER ===")

    registry = orchestrator.build_worker_provider_registry()
    provider = orchestrator.resolve_worker_provider(
        registry,
        "lm_studio",
    )

    models = orchestrator.resolve_provider_models(provider)

    check(
        "live LM Studio inventory is non-empty",
        bool(models),
    )

    print(f"Discovered models: {len(models)}")

    for model in models:
        print(f"  - {model}")

    model = orchestrator.bind_provider_worker(
        "qwen",
        provider,
    )

    print(f"Selected live model: {model}")

    request = orchestrator.WorkerExecutionRequest(
        execution_id="n11-live-x0001",
        task_id="n11-live-provider",
        attempt_id="n11-live-d001",
        provider_id="lm_studio",
        model_id=model,
        system_prompt=(
            "You are a deterministic integration-test worker. "
            "Return only the exact requested token."
        ),
        user_prompt=(
            "Return exactly N11-LIVE-OK and nothing else."
        ),
        temperature=0.0,
        max_output_tokens=32,
        budget=orchestrator.ExecutionBudget(
            fallback_timeout_seconds=120,
            absolute_timeout_seconds=180,
        ),
    )

    result = orchestrator.supervise_provider_execution(
        provider,
        request,
    )

    check(
        "live provider execution completed",
        result.state is orchestrator.ExecutionState.COMPLETED,
    )

    check(
        "live provider execution returned output",
        isinstance(result.output, str),
    )

    check(
        "live provider output matches qualification token",
        result.output is not None
        and result.output.strip() == "N11-LIVE-OK",
    )

    check(
        "live provider execution has no trusted error",
        result.error is None,
    )

    check(
        "live provider execution recorded telemetry",
        result.telemetry.started_at is not None
        and result.telemetry.completed_at is not None
        and result.telemetry.elapsed_seconds is not None,
    )

    print(
        "Live provider elapsed seconds: "
        f"{result.telemetry.elapsed_seconds}"
    )


def test_live_external_request_subprocess() -> None:
    print("\n=== N11 LIVE EXTERNAL-REQUEST SUBPROCESS ===")

    python_exe = ROOT / ".venv" / "Scripts" / "python.exe"
    orchestrator_path = ROOT / "orchestrator.py"

    projects_dir = ROOT / "projects"
    state_dir = ROOT / "state"
    traces_dir = ROOT / "traces"
    trace = traces_dir / "_external-supervisor.jsonl"

    state_dir_existed = state_dir.exists()
    traces_dir_existed = traces_dir.exists()
    trace_existed = trace.exists()

    token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    project_name = f"n11-live-{token.lower()}"
    task_id = f"{project_name}-task"
    request_id = f"n11-live-status-{token}"

    project = projects_dir / f"{project_name}.yaml"
    task = state_dir / f"{task_id}.yaml"

    policy = orchestrator.load_yaml(orchestrator.POLICY_PATH)
    policy_version = str(policy["policy"]["version"])

    project_payload = {
        "schema_version": "1.0",
        "project": {
            "name": project_name,
            "path": ROOT.as_posix(),
        },
        "model_routing": {
            "local_worker_provider_id": "lm_studio",
        },
        "budget": {
            "max_worker_calls": 1,
            "max_parallel_workers": 1,
            "local_worker_timeout_seconds": 120,
        },
    }

    task_payload = {
        "schema_version": "1.0",
        "task": {
            "task_id": task_id,
            "active_project": project_name,
            "status": "INTAKE",
        },
        "budget": {
            "worker_calls_used": 0,
        },
        "trace": {
            "policy_version": policy_version,
            "project_profile_version": "1.0",
        },
    }

    def matching_trace_records() -> list[dict[str, object]]:
        if not trace.exists():
            return []

        matches: list[dict[str, object]] = []

        for line in trace.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            if (
                isinstance(record, dict)
                and record.get("request_id") == request_id
            ):
                matches.append(record)

        return matches

    def remove_own_trace_records() -> None:
        if not trace.exists():
            return

        kept: list[str] = []

        for line in trace.read_text(
            encoding="utf-8"
        ).splitlines(keepends=True):
            stripped = line.strip()

            if not stripped:
                kept.append(line)
                continue

            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                kept.append(line)
                continue

            if not (
                isinstance(record, dict)
                and record.get("request_id") == request_id
            ):
                kept.append(line)

        if kept:
            with trace.open(
                "w",
                encoding="utf-8",
                newline="\n",
            ) as fh:
                fh.writelines(kept)
        else:
            trace.unlink()

    try:
        projects_dir.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)

        project.write_text(
            json.dumps(project_payload, indent=2) + "\n",
            encoding="utf-8",
        )
        task.write_text(
            json.dumps(task_payload, indent=2) + "\n",
            encoding="utf-8",
        )

        check(
            "temporary reference project exists",
            project.is_file(),
        )
        check(
            "temporary reference task exists",
            task.is_file(),
        )

        task_hash_before = sha256(task)

        request = {
            "schema_version": (
                orchestrator.EXTERNAL_SUPERVISOR_SCHEMA_VERSION
            ),
            "request_id": request_id,
            "supervisor_id": "n11-live-integration",
            "operation": "status",
            "task": task.name,
            "payload": {},
        }

        completed = subprocess.run(
            [
                str(python_exe),
                str(orchestrator_path),
                "external-request",
            ],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            cwd=ROOT,
            check=False,
        )

        task_hash_after = sha256(task)

        check(
            "external-request subprocess exits 0",
            completed.returncode == 0,
        )

        check(
            "external-request subprocess stderr is empty",
            completed.stderr == "",
        )

        stdout_lines = completed.stdout.splitlines()

        check(
            "external-request emits exactly one stdout line",
            len(stdout_lines) == 1,
        )

        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise IntegrationFailure(
                "external-request stdout is not valid JSON"
            ) from exc

        check(
            "external-request response is completed",
            response.get("disposition") == "completed",
        )

        check(
            "external-request preserves request ID",
            response.get("request_id") == request_id,
        )

        check(
            "external-request trusted result is zero",
            response.get("result") == 0,
        )

        check(
            "read-only status request does not mutate task",
            task_hash_before == task_hash_after,
        )

        check(
            "accepted and completed provenance appended",
            len(matching_trace_records()) == 2,
        )

    finally:
        project.unlink(missing_ok=True)
        task.unlink(missing_ok=True)

        remove_own_trace_records()

        if trace_existed and not trace.exists():
            trace.parent.mkdir(parents=True, exist_ok=True)
            trace.touch()

        if (
            not state_dir_existed
            and state_dir.exists()
            and not any(state_dir.iterdir())
        ):
            state_dir.rmdir()

        if (
            not traces_dir_existed
            and traces_dir.exists()
            and not any(traces_dir.iterdir())
        ):
            traces_dir.rmdir()

def main() -> int:
    print("Trusted Hybrid AI Orchestrator Live Integration")
    print("N11 release qualification")
    print("============================================")

    try:
        test_live_lm_studio_provider()
        test_live_external_request_subprocess()

    except IntegrationFailure as exc:
        print(f"\nFAIL: {exc}")
        return 1

    print("\nN11 live integration qualification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
