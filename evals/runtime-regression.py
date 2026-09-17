from __future__ import annotations

import copy
import json
import sys
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import orchestrator


class RegressionFailure(RuntimeError):
    pass


def check(label: str, condition: bool) -> None:
    if not condition:
        raise RegressionFailure(label)

    print(f"PASS: {label}")


def expect_orchestrator_error(
    label: str,
    action: Callable[[], object],
) -> None:
    try:
        action()
    except orchestrator.OrchestratorError:
        print(f"PASS: {label}")
        return

    raise RegressionFailure(label)


class CancelProvider:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, bool]] = []

    def cancel(
        self,
        execution_id: str,
        *,
        force: bool = False,
    ) -> bool:
        self.calls.append((execution_id, force))
        return self.result  # type: ignore[return-value]


class StubProvider:
    def __init__(
        self,
        *,
        provider_ids: list[str] | None = None,
        capabilities: object | None = None,
        models: object | None = None,
        output: object = "OK",
    ) -> None:
        self.provider_ids = provider_ids or ["stub"]
        self.capabilities_value = (
            capabilities
            if capabilities is not None
            else orchestrator.ProviderCapabilities(
                streaming=False,
                cooperative_cancel=False,
                transport_cancel=False,
                backend_cancel=False,
                force_terminate=False,
            )
        )
        self.models_value = (
            models if models is not None else ["qwen/qwen3.5-9b"]
        )
        self.output = output
        self.provider_id_calls = 0
        self.execute_calls: list[
            tuple[orchestrator.WorkerExecutionRequest, float]
        ] = []

    def provider_id(self) -> str:
        index = min(
            self.provider_id_calls,
            len(self.provider_ids) - 1,
        )
        self.provider_id_calls += 1
        return self.provider_ids[index]

    def capabilities(self) -> orchestrator.ProviderCapabilities:
        return self.capabilities_value  # type: ignore[return-value]

    def list_models(self) -> list[str]:
        return self.models_value  # type: ignore[return-value]

    def execute_transport(
        self,
        request: orchestrator.WorkerExecutionRequest,
        timeout_seconds: float,
    ) -> str:
        self.execute_calls.append((request, timeout_seconds))
        return self.output  # type: ignore[return-value]

    def cancel(
        self,
        execution_id: str,
        *,
        force: bool = False,
    ) -> bool:
        return False


def completed_result(
    *,
    execution_id: str = "x0001",
    output: str | None = "OK",
    error: str | None = None,
    state: orchestrator.ExecutionState = (
        orchestrator.ExecutionState.COMPLETED
    ),
) -> orchestrator.WorkerExecutionResult:
    return orchestrator.WorkerExecutionResult(
        execution_id=execution_id,
        state=state,
        output=output,
        error=error,
        telemetry=orchestrator.ExecutionTelemetry(),
    )


def worker_request(
    *,
    provider_id: str = "stub",
) -> orchestrator.WorkerExecutionRequest:
    return orchestrator.WorkerExecutionRequest(
        execution_id="x0001",
        task_id="task-001",
        attempt_id="d001",
        provider_id=provider_id,
        model_id="qwen/qwen3.5-9b",
        system_prompt="system",
        user_prompt="user",
        temperature=0.2,
        max_output_tokens=128,
        budget=orchestrator.ExecutionBudget(
            fallback_timeout_seconds=60,
        ),
    )


def test_n4_execution_id_allocator() -> None:
    print("\n=== N4 EXECUTION ID ALLOCATION ===")

    task: dict[str, object] = {}
    before = copy.deepcopy(task)

    check(
        "empty task -> x0001",
        orchestrator.next_execution_id(task) == "x0001",
    )
    check(
        "empty task not mutated",
        task == before,
    )

    task = {
        "delegation": {
            "attempts": [
                {"execution": {"execution_id": "x0001"}},
                {"execution": {"execution_id": "x0002"}},
            ]
        }
    }
    before = copy.deepcopy(task)

    check(
        "sequential IDs -> x0003",
        orchestrator.next_execution_id(task) == "x0003",
    )
    check(
        "sequential task not mutated",
        task == before,
    )

    task = {
        "delegation": {
            "attempts": [
                {"execution": {"execution_id": "x0001"}},
                {"execution": {"execution_id": "x0003"}},
            ]
        }
    }

    check(
        "allocator fills first gap",
        orchestrator.next_execution_id(task) == "x0002",
    )

    task = {
        "delegation": {
            "attempts": [
                None,
                "bad",
                {},
                {"execution": None},
                {"execution": {"execution_id": ""}},
                {"execution": {"execution_id": 7}},
                {"execution": {"execution_id": "x0001"}},
            ]
        }
    }

    check(
        "malformed records ignored",
        orchestrator.next_execution_id(task) == "x0002",
    )


def test_n4_result_admissibility() -> None:
    print("\n=== N4 RESULT ADMISSIBILITY ===")

    check(
        "matching completed RUNNING result admissible",
        orchestrator.execution_output_admissible(
            active_execution_id="x0001",
            current_state=orchestrator.ExecutionState.RUNNING,
            result=completed_result(),
        ),
    )

    check(
        "wrong execution ID rejected",
        not orchestrator.execution_output_admissible(
            active_execution_id="x0001",
            current_state=orchestrator.ExecutionState.RUNNING,
            result=completed_result(execution_id="x0002"),
        ),
    )

    check(
        "non-RUNNING trusted state rejected",
        not orchestrator.execution_output_admissible(
            active_execution_id="x0001",
            current_state=orchestrator.ExecutionState.CANCELLING,
            result=completed_result(),
        ),
    )

    check(
        "non-COMPLETED result rejected",
        not orchestrator.execution_output_admissible(
            active_execution_id="x0001",
            current_state=orchestrator.ExecutionState.RUNNING,
            result=completed_result(
                state=orchestrator.ExecutionState.FAILED,
            ),
        ),
    )

    check(
        "missing output rejected",
        not orchestrator.execution_output_admissible(
            active_execution_id="x0001",
            current_state=orchestrator.ExecutionState.RUNNING,
            result=completed_result(output=None),
        ),
    )

    check(
        "result carrying error rejected",
        not orchestrator.execution_output_admissible(
            active_execution_id="x0001",
            current_state=orchestrator.ExecutionState.RUNNING,
            result=completed_result(error="failure"),
        ),
    )


def test_n4_cancellation_resolution() -> None:
    print("\n=== N4 CANCELLATION RESOLUTION ===")

    provider = CancelProvider(True)

    state = orchestrator.resolve_execution_cancellation(
        provider,
        execution_id="x0007",
        current_state=orchestrator.ExecutionState.CANCELLING,
        force=True,
    )

    check(
        "confirmed cancellation -> CANCELLED",
        state is orchestrator.ExecutionState.CANCELLED,
    )
    check(
        "execution ID and force forwarded unchanged",
        provider.calls == [("x0007", True)],
    )

    provider = CancelProvider(False)

    state = orchestrator.resolve_execution_cancellation(
        provider,
        execution_id="x0008",
        current_state=orchestrator.ExecutionState.CANCELLING,
    )

    check(
        "unconfirmed cancellation -> CANCEL_FAILED",
        state is orchestrator.ExecutionState.CANCEL_FAILED,
    )

    expect_orchestrator_error(
        "empty execution ID rejected",
        lambda: orchestrator.resolve_execution_cancellation(
            CancelProvider(True),
            execution_id="",
            current_state=orchestrator.ExecutionState.CANCELLING,
        ),
    )

    expect_orchestrator_error(
        "cancellation only resolves from CANCELLING",
        lambda: orchestrator.resolve_execution_cancellation(
            CancelProvider(True),
            execution_id="x0009",
            current_state=orchestrator.ExecutionState.CANCEL_REQUESTED,
        ),
    )

    expect_orchestrator_error(
        "provider cancellation must return bool",
        lambda: orchestrator.resolve_execution_cancellation(
            CancelProvider("yes"),
            execution_id="x0010",
            current_state=orchestrator.ExecutionState.CANCELLING,
        ),
    )


def test_n5_budget_resolution() -> None:
    print("\n=== N5 EXECUTION BUDGET RESOLUTION ===")

    budget = orchestrator.resolve_execution_budget(
        {
            "budget": {
                "local_worker_timeout_seconds": 90,
            }
        }
    )

    check(
        "legacy timeout becomes fallback",
        budget.fallback_timeout_seconds == 90.0,
    )
    check(
        "unspecified advanced budgets remain None",
        budget.model_load_timeout_seconds is None
        and budget.first_progress_timeout_seconds is None
        and budget.stall_timeout_seconds is None
        and budget.absolute_timeout_seconds is None,
    )

    budget = orchestrator.resolve_execution_budget(
        {
            "budget": {
                "local_worker_timeout_seconds": 90,
                "worker_execution": {
                    "fallback_timeout_seconds": 120,
                    "model_load_timeout_seconds": 30,
                    "first_progress_timeout_seconds": 15,
                    "stall_timeout_seconds": 20,
                    "absolute_timeout_seconds": 180,
                },
            }
        }
    )

    check(
        "worker execution overrides resolved exactly",
        budget
        == orchestrator.ExecutionBudget(
            fallback_timeout_seconds=120.0,
            model_load_timeout_seconds=30.0,
            first_progress_timeout_seconds=15.0,
            stall_timeout_seconds=20.0,
            absolute_timeout_seconds=180.0,
        ),
    )

    expect_orchestrator_error(
        "boolean legacy timeout rejected",
        lambda: orchestrator.resolve_execution_budget(
            {
                "budget": {
                    "local_worker_timeout_seconds": True,
                }
            }
        ),
    )

    expect_orchestrator_error(
        "absolute timeout below fallback rejected",
        lambda: orchestrator.resolve_execution_budget(
            {
                "budget": {
                    "local_worker_timeout_seconds": 90,
                    "worker_execution": {
                        "fallback_timeout_seconds": 120,
                        "absolute_timeout_seconds": 100,
                    },
                }
            }
        ),
    )


def test_n5_watchdog() -> None:
    print("\n=== N5 EXECUTION WATCHDOG ===")

    budget = orchestrator.ExecutionBudget(
        fallback_timeout_seconds=60,
        first_progress_timeout_seconds=10,
        stall_timeout_seconds=15,
        absolute_timeout_seconds=120,
    )

    decision = orchestrator.evaluate_execution_watchdog(
        budget,
        elapsed_seconds=20,
        progress_observable=False,
    )

    check(
        "unobservable progress stays RUNNING",
        decision.state is orchestrator.ExecutionState.RUNNING
        and decision.reason == "progress_unobservable",
    )

    decision = orchestrator.evaluate_execution_watchdog(
        budget,
        elapsed_seconds=120,
        progress_observable=False,
    )

    check(
        "absolute timeout has watchdog precedence",
        decision.state is orchestrator.ExecutionState.TIMED_OUT
        and decision.reason == "absolute_timeout_exceeded",
    )

    decision = orchestrator.evaluate_execution_watchdog(
        budget,
        elapsed_seconds=10,
        progress_observable=True,
    )

    check(
        "missing first progress can stall",
        decision.state is orchestrator.ExecutionState.STALLED
        and decision.reason == "first_progress_timeout_exceeded",
    )

    decision = orchestrator.evaluate_execution_watchdog(
        budget,
        elapsed_seconds=30,
        progress_observable=True,
        first_progress_elapsed_seconds=5,
        last_progress_elapsed_seconds=15,
    )

    check(
        "stale observable progress can stall",
        decision.state is orchestrator.ExecutionState.STALLED
        and decision.reason == "stall_timeout_exceeded",
    )

    decision = orchestrator.evaluate_execution_watchdog(
        budget,
        elapsed_seconds=20,
        progress_observable=True,
        first_progress_elapsed_seconds=5,
        last_progress_elapsed_seconds=18,
    )

    check(
        "recent progress stays RUNNING",
        decision.state is orchestrator.ExecutionState.RUNNING
        and decision.reason == "progress_within_budget",
    )

    expect_orchestrator_error(
        "progress timestamps rejected when progress unobservable",
        lambda: orchestrator.evaluate_execution_watchdog(
            budget,
            elapsed_seconds=5,
            progress_observable=False,
            first_progress_elapsed_seconds=1,
            last_progress_elapsed_seconds=1,
        ),
    )


def test_n5_timeout_prediction() -> None:
    print("\n=== N5 TIMEOUT PREDICTION ===")

    budget = orchestrator.ExecutionBudget(
        fallback_timeout_seconds=60,
        absolute_timeout_seconds=180,
    )

    prediction = orchestrator.predict_execution_timeout(
        budget,
        max_output_tokens=100,
    )

    check(
        "missing rate data uses fallback",
        prediction.timeout_seconds == 60
        and prediction.source == "fallback",
    )

    prediction = orchestrator.predict_execution_timeout(
        budget,
        max_output_tokens=200,
        estimated_input_tokens=1000,
        prefill_tokens_per_second=100,
        decode_tokens_per_second=10,
        safety_factor=2,
    )

    check(
        "rate estimate uses prefill plus decode with safety factor",
        prediction.timeout_seconds == 60
        and prediction.source == "rate_estimate"
        and prediction.estimated_prefill_seconds == 10
        and prediction.estimated_decode_seconds == 20,
    )

    prediction = orchestrator.predict_execution_timeout(
        orchestrator.ExecutionBudget(
            fallback_timeout_seconds=60,
            absolute_timeout_seconds=90,
        ),
        max_output_tokens=1000,
        estimated_input_tokens=1000,
        prefill_tokens_per_second=10,
        decode_tokens_per_second=5,
        safety_factor=2,
    )

    check(
        "absolute timeout caps rate estimate",
        prediction.timeout_seconds == 90,
    )

    expect_orchestrator_error(
        "boolean max output tokens rejected",
        lambda: orchestrator.predict_execution_timeout(
            budget,
            max_output_tokens=True,
        ),
    )


def test_n6_worker_execution_supervisor() -> None:
    print("\n=== N6 WORKER EXECUTION SUPERVISOR ===")

    request = worker_request()
    calls: list[float] = []

    def successful_transport(
        received: orchestrator.WorkerExecutionRequest,
        timeout_seconds: float,
    ) -> str:
        check(
            "supervisor forwards exact request object",
            received is request,
        )
        calls.append(timeout_seconds)
        return "worker-output"

    result = orchestrator.supervise_worker_execution(
        request,
        successful_transport,
    )

    check(
        "successful transport -> COMPLETED",
        result.state is orchestrator.ExecutionState.COMPLETED
        and result.execution_id == "x0001"
        and result.output == "worker-output"
        and result.error is None,
    )
    check(
        "resolved fallback timeout forwarded",
        calls == [60],
    )
    check(
        "completion telemetry recorded",
        result.telemetry.started_at is not None
        and result.telemetry.completed_at is not None
        and result.telemetry.elapsed_seconds is not None,
    )

    def timeout_transport(
        received: orchestrator.WorkerExecutionRequest,
        timeout_seconds: float,
    ) -> str:
        raise orchestrator.WorkerTimeoutError("worker timeout")

    result = orchestrator.supervise_worker_execution(
        request,
        timeout_transport,
    )

    check(
        "WorkerTimeoutError -> TIMED_OUT",
        result.state is orchestrator.ExecutionState.TIMED_OUT
        and result.output is None
        and result.error == "worker timeout",
    )

    def unexpected_transport(
        received: orchestrator.WorkerExecutionRequest,
        timeout_seconds: float,
    ) -> str:
        raise ValueError("boom")

    result = orchestrator.supervise_worker_execution(
        request,
        unexpected_transport,
    )

    check(
        "unexpected transport exception contained as FAILED",
        result.state is orchestrator.ExecutionState.FAILED
        and result.output is None
        and result.error is not None
        and "Worker transport raised unexpected ValueError: boom"
        in result.error,
    )

    def malformed_transport(
        received: orchestrator.WorkerExecutionRequest,
        timeout_seconds: float,
    ) -> str:
        return 7  # type: ignore[return-value]

    result = orchestrator.supervise_worker_execution(
        request,
        malformed_transport,
    )

    check(
        "non-string transport output -> FAILED",
        result.state is orchestrator.ExecutionState.FAILED
        and result.output is None
        and result.error is not None
        and "invalid output type: int" in result.error,
    )

    def interrupt_transport(
        received: orchestrator.WorkerExecutionRequest,
        timeout_seconds: float,
    ) -> str:
        raise KeyboardInterrupt()

    try:
        orchestrator.supervise_worker_execution(
            request,
            interrupt_transport,
        )
    except KeyboardInterrupt:
        interrupt_propagates = True
    else:
        interrupt_propagates = False

    check(
        "KeyboardInterrupt propagates through supervisor",
        interrupt_propagates,
    )


def test_n7_provider_boundary() -> None:
    print("\n=== N7 PROVIDER BOUNDARY ===")

    provider = StubProvider()
    request = worker_request(provider_id="stub")

    result = orchestrator.supervise_provider_execution(
        provider,
        request,
    )

    check(
        "provider bridge completes through trusted supervisor",
        result.state is orchestrator.ExecutionState.COMPLETED
        and result.output == "OK",
    )
    check(
        "provider transport invoked once",
        len(provider.execute_calls) == 1,
    )

    mismatch_provider = StubProvider(provider_ids=["provider-a"])

    expect_orchestrator_error(
        "provider/request identity mismatch rejected",
        lambda: orchestrator.supervise_provider_execution(
            mismatch_provider,
            worker_request(provider_id="provider-b"),
        ),
    )

    check(
        "identity mismatch rejected before transport",
        mismatch_provider.execute_calls == [],
    )

    registry: dict[str, orchestrator.WorkerProvider] = {}
    registered = StubProvider(provider_ids=["provider-a"])

    orchestrator.register_worker_provider(
        registry,
        registered,
    )

    check(
        "registered provider resolves by exact ID",
        orchestrator.resolve_worker_provider(
            registry,
            "provider-a",
        )
        is registered,
    )

    expect_orchestrator_error(
        "duplicate provider registration rejected",
        lambda: orchestrator.register_worker_provider(
            registry,
            StubProvider(provider_ids=["provider-a"]),
        ),
    )

    drift_registry: dict[str, orchestrator.WorkerProvider] = {}
    drifting = StubProvider(
        provider_ids=["provider-a", "provider-b"],
    )

    orchestrator.register_worker_provider(
        drift_registry,
        drifting,
    )

    expect_orchestrator_error(
        "registered provider identity drift rejected",
        lambda: orchestrator.resolve_worker_provider(
            drift_registry,
            "provider-a",
        ),
    )

    provider = StubProvider()
    capabilities = orchestrator.resolve_provider_capabilities(
        provider
    )

    check(
        "valid capabilities preserved",
        capabilities
        == orchestrator.ProviderCapabilities(
            streaming=False,
            cooperative_cancel=False,
            transport_cancel=False,
            backend_cancel=False,
            force_terminate=False,
        ),
    )

    invalid_capabilities = StubProvider(
        capabilities=orchestrator.ProviderCapabilities(
            streaming=1,  # type: ignore[arg-type]
            cooperative_cancel=False,
            transport_cancel=False,
            backend_cancel=False,
            force_terminate=False,
        )
    )

    expect_orchestrator_error(
        "non-bool provider capability rejected",
        lambda: orchestrator.resolve_provider_capabilities(
            invalid_capabilities
        ),
    )

    capability_drift = StubProvider(
        provider_ids=["provider-a", "provider-b"],
    )

    expect_orchestrator_error(
        "provider identity drift during capability inspection rejected",
        lambda: orchestrator.resolve_provider_capabilities(
            capability_drift
        ),
    )

    inventory_provider = StubProvider(
        models=[
            "qwen/qwen3.5-4b",
            "qwen/qwen3.5-9b",
        ]
    )

    inventory = orchestrator.resolve_provider_models(
        inventory_provider
    )

    check(
        "provider inventory order and IDs preserved exactly",
        inventory
        == [
            "qwen/qwen3.5-4b",
            "qwen/qwen3.5-9b",
        ],
    )

    expect_orchestrator_error(
        "duplicate provider model ID rejected",
        lambda: orchestrator.resolve_provider_models(
            StubProvider(
                models=[
                    "qwen/qwen3.5-9b",
                    "qwen/qwen3.5-9b",
                ]
            )
        ),
    )

    model_drift = StubProvider(
        provider_ids=["provider-a", "provider-b"],
    )

    expect_orchestrator_error(
        "provider identity drift during inventory rejected",
        lambda: orchestrator.resolve_provider_models(
            model_drift
        ),
    )

    bound = orchestrator.bind_provider_worker(
        "qwen",
        StubProvider(
            models=[
                "qwen/qwen3.5-4b",
                "qwen/qwen3.5-9b",
            ]
        ),
    )

    check(
        "role binding follows canonical model priority",
        bound == "qwen/qwen3.5-9b",
    )

    expect_orchestrator_error(
        "undefined worker role rejected",
        lambda: orchestrator.bind_provider_worker(
            "unknown-role",
            StubProvider(),
        ),
    )


def test_n8_lm_studio_adapter() -> None:
    print("\n=== N8 LM STUDIO ADAPTER ===")

    provider = orchestrator.LMStudioProvider()

    check(
        "LM Studio provider ID is canonical",
        provider.provider_id() == "lm_studio",
    )

    check(
        "LM Studio capabilities remain conservative",
        provider.capabilities()
        == orchestrator.ProviderCapabilities(
            streaming=False,
            cooperative_cancel=False,
            transport_cancel=False,
            backend_cancel=False,
            force_terminate=False,
        ),
    )

    original_models = orchestrator.lm_studio_models
    original_call_worker = orchestrator.call_worker

    calls: list[dict[str, object]] = []

    try:
        orchestrator.lm_studio_models = lambda: [
            "model-a",
            "model-b",
        ]

        inventory = provider.list_models()

        check(
            "LM Studio inventory delegates unchanged",
            inventory == ["model-a", "model-b"],
        )

        def fake_call_worker(
            *,
            model: str,
            system_prompt: str,
            user_prompt: str,
            timeout: float,
        ) -> str:
            calls.append(
                {
                    "model": model,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "timeout": timeout,
                }
            )
            return "adapter-output"

        orchestrator.call_worker = fake_call_worker

        request = worker_request(provider_id="lm_studio")

        output = provider.execute_transport(
            request,
            42.5,
        )

        check(
            "LM Studio transport output preserved",
            output == "adapter-output",
        )

        check(
            "LM Studio transport forwards exact request fields",
            calls
            == [
                {
                    "model": request.model_id,
                    "system_prompt": request.system_prompt,
                    "user_prompt": request.user_prompt,
                    "timeout": 42.5,
                }
            ],
        )

        check(
            "LM Studio cancellation remains unconfirmed",
            provider.cancel("x0001") is False,
        )

        registry = orchestrator.build_worker_provider_registry()

        check(
            "default registry contains LM Studio provider",
            set(registry) == {"lm_studio"}
            and isinstance(
                registry["lm_studio"],
                orchestrator.LMStudioProvider,
            ),
        )

    finally:
        orchestrator.lm_studio_models = original_models
        orchestrator.call_worker = original_call_worker

    project = {
        "model_routing": {
            "local_worker_provider_id": "lm_studio",
        }
    }

    check(
        "project provider ID resolves exactly",
        orchestrator.resolve_project_worker_provider_id(project)
        == "lm_studio",
    )

    registry = {
        "lm_studio": orchestrator.LMStudioProvider(),
    }

    check(
        "project provider resolves through registry",
        orchestrator.resolve_project_worker_provider(
            project,
            registry,
        )
        is registry["lm_studio"],
    )

    expect_orchestrator_error(
        "project provider ID surrounding whitespace rejected",
        lambda: orchestrator.resolve_project_worker_provider_id(
            {
                "model_routing": {
                    "local_worker_provider_id": " lm_studio",
                }
            }
        ),
    )


def test_n9_external_supervisor_boundary() -> None:
    print("\n=== N9 EXTERNAL SUPERVISOR BOUNDARY ===")

    check(
        "external operation set remains closed",
        {
            operation.value
            for operation in orchestrator.ExternalSupervisorOperation
        }
        == {
            "status",
            "preflight",
            "delegate",
            "verify",
        },
    )

    status_mapping = {
        "schema_version": (
            orchestrator.EXTERNAL_SUPERVISOR_SCHEMA_VERSION
        ),
        "request_id": "n11-r0001",
        "supervisor_id": "n11-regression",
        "operation": "status",
        "task": "n11-task.yaml",
        "payload": {},
    }

    request = (
        orchestrator.external_supervisor_request_from_mapping(
            status_mapping
        )
    )

    check(
        "valid mapping becomes typed request",
        isinstance(
            request,
            orchestrator.ExternalSupervisorRequest,
        )
        and request.operation
        is orchestrator.ExternalSupervisorOperation.STATUS,
    )

    unknown = dict(status_mapping)
    unknown["approval"] = "not-allowed"

    expect_orchestrator_error(
        "unknown top-level authority field rejected",
        lambda: orchestrator.external_supervisor_request_from_mapping(
            unknown
        ),
    )

    authority_operation = dict(status_mapping)
    authority_operation["operation"] = "human-approval"

    expect_orchestrator_error(
        "authority-bearing operation cannot be constructed",
        lambda: orchestrator.external_supervisor_request_from_mapping(
            authority_operation
        ),
    )

    delegate_mapping = {
        **status_mapping,
        "operation": "delegate",
        "payload": {
            "role": "qwen",
            "work_product": "EXTRACT",
            "reason": "bounded extraction",
            "expected_output": "evidence packet",
            "context_file": ["a.py"],
            "context_symbol": ["a.py:function_a"],
        },
    }

    delegate_request = (
        orchestrator.external_supervisor_request_from_mapping(
            delegate_mapping
        )
    )

    original_context = delegate_mapping["payload"]["context_file"]
    original_context.append("mutated.py")

    check(
        "delegate context list is caller-isolated",
        delegate_request.payload["context_file"] == ["a.py"],
    )

    smuggled_delegate = {
        **delegate_mapping,
        "payload": {
            **delegate_mapping["payload"],
            "approval": "A2_NOT_ALLOWED",
        },
    }

    expect_orchestrator_error(
        "delegate authority smuggling rejected",
        lambda: orchestrator.external_supervisor_request_from_mapping(
            smuggled_delegate
        ),
    )

    duplicate_json = '''
    {
      "schema_version": "1.0",
      "request_id": "n11-r0001",
      "request_id": "n11-r9999",
      "supervisor_id": "n11-regression",
      "operation": "status",
      "task": "n11-task.yaml",
      "payload": {}
    }
    '''

    expect_orchestrator_error(
        "duplicate JSON keys rejected",
        lambda: orchestrator.external_supervisor_request_from_json(
            duplicate_json
        ),
    )

    with TemporaryDirectory() as temp_dir:
        original_state_dir = orchestrator.STATE_DIR

        try:
            orchestrator.STATE_DIR = Path(temp_dir)

            resolved = (
                orchestrator.resolve_external_supervisor_task_path(
                    "example"
                )
            )

            check(
                "extensionless task resolves inside trusted state root",
                resolved
                == (Path(temp_dir) / "example.yaml").resolve(),
            )

            expect_orchestrator_error(
                "task path traversal rejected",
                lambda: (
                    orchestrator.resolve_external_supervisor_task_path(
                        "../escape.yaml"
                    )
                ),
            )

            expect_orchestrator_error(
                "task directory separator rejected",
                lambda: (
                    orchestrator.resolve_external_supervisor_task_path(
                        "subdir/task.yaml"
                    )
                ),
            )

            expect_orchestrator_error(
                "unsupported task suffix rejected",
                lambda: (
                    orchestrator.resolve_external_supervisor_task_path(
                        "task.json"
                    )
                ),
            )

        finally:
            orchestrator.STATE_DIR = original_state_dir

    originals = {
        "append_external_supervisor_trace": (
            orchestrator.append_external_supervisor_trace
        ),
        "status": orchestrator.status,
        "delegate": orchestrator.delegate,
    }

    events: list[tuple[str, dict[str, object]]] = []

    try:
        def trace_stub(
            request: orchestrator.ExternalSupervisorRequest,
            event: str,
            **kwargs: object,
        ) -> None:
            events.append((event, dict(kwargs)))

        orchestrator.append_external_supervisor_trace = trace_stub
        orchestrator.status = lambda task: 17

        result = orchestrator.dispatch_external_supervisor_request(
            request
        )

        check(
            "status dispatch returns trusted result",
            result == 17,
        )

        check(
            "status dispatch records accepted then completed",
            [event for event, _ in events]
            == [
                "external_request_accepted",
                "external_request_completed",
            ],
        )

        captured_delegate: list[object] = []

        def delegate_stub(args: object) -> int:
            captured_delegate.append(args)
            return 23

        orchestrator.delegate = delegate_stub
        events.clear()

        result = orchestrator.dispatch_external_supervisor_request(
            delegate_request
        )

        check(
            "delegate dispatch returns trusted result",
            result == 23,
        )

        args = captured_delegate[0]

        check(
            "delegate dispatcher constructs bounded namespace",
            vars(args)
            == {
                "task": str(
                    (
                        orchestrator.STATE_DIR
                        / "n11-task.yaml"
                    ).resolve()
                ),
                "role": "qwen",
                "work_product": "EXTRACT",
                "reason": "bounded extraction",
                "expected_output": "evidence packet",
                "context_file": ["a.py"],
                "context_symbol": ["a.py:function_a"],
                "retry_of": None,
                "retry_kind": None,
            },
        )

        events.clear()

        def failing_status(task: str) -> int:
            raise orchestrator.OrchestratorError(
                "trusted status failure"
            )

        orchestrator.status = failing_status

        try:
            orchestrator.dispatch_external_supervisor_request(
                request
            )
        except orchestrator.OrchestratorError as exc:
            preserved = str(exc) == "trusted status failure"
        else:
            preserved = False

        check(
            "trusted operation failure propagates unchanged",
            preserved,
        )

        check(
            "failure provenance records type without raw error",
            events[-1][0] == "external_request_failed"
            and events[-1][1].get("error_type")
            == "OrchestratorError"
            and "error" not in events[-1][1],
        )

    finally:
        for name, value in originals.items():
            setattr(orchestrator, name, value)


def test_n10_reference_protocol() -> None:
    print("\n=== N10 REFERENCE PROTOCOL ===")

    valid_json = json.dumps(
        {
            "schema_version": (
                orchestrator.EXTERNAL_SUPERVISOR_SCHEMA_VERSION
            ),
            "request_id": "n10-r0001",
            "supervisor_id": "n11-regression",
            "operation": "status",
            "task": "n11-task.yaml",
            "payload": {},
        }
    )

    original_dispatch = (
        orchestrator.dispatch_external_supervisor_request
    )

    try:
        response = (
            orchestrator.execute_external_supervisor_json_request(
                "{malformed"
            )
        )

        check(
            "malformed protocol request is rejected",
            response["disposition"] == "rejected"
            and response["request_id"] is None,
        )

        def success_dispatch(
            request: orchestrator.ExternalSupervisorRequest,
        ) -> int:
            print("legacy stdout")
            print("legacy stderr", file=sys.stderr)
            return 17

        orchestrator.dispatch_external_supervisor_request = (
            success_dispatch
        )

        outer_stdout = StringIO()
        outer_stderr = StringIO()

        with (
            redirect_stdout(outer_stdout),
            redirect_stderr(outer_stderr),
        ):
            response = (
                orchestrator.execute_external_supervisor_json_request(
                    valid_json
                )
            )

        check(
            "successful protocol request completes",
            response["disposition"] == "completed"
            and response["request_id"] == "n10-r0001"
            and response["result"] == 17,
        )

        check(
            "legacy output captured inside protocol response",
            response["output"] == "legacy stdout\n"
            and response["diagnostics"] == "legacy stderr\n",
        )

        check(
            "legacy output does not escape protocol executor",
            outer_stdout.getvalue() == ""
            and outer_stderr.getvalue() == "",
        )

        def trusted_failure(
            request: orchestrator.ExternalSupervisorRequest,
        ) -> int:
            raise orchestrator.OrchestratorError(
                "trusted refusal"
            )

        orchestrator.dispatch_external_supervisor_request = (
            trusted_failure
        )

        response = (
            orchestrator.execute_external_supervisor_json_request(
                valid_json
            )
        )

        check(
            "trusted failure remains explicit protocol failure",
            response["disposition"] == "failed"
            and response["error_type"] == "OrchestratorError"
            and response["error"] == "trusted refusal",
        )

        def internal_failure(
            request: orchestrator.ExternalSupervisorRequest,
        ) -> int:
            print("SECRET-STDOUT")
            print("SECRET-STDERR", file=sys.stderr)
            raise ValueError("SECRET-ERROR")

        orchestrator.dispatch_external_supervisor_request = (
            internal_failure
        )

        response = (
            orchestrator.execute_external_supervisor_json_request(
                valid_json
            )
        )

        check(
            "unexpected failure is redacted",
            response["disposition"] == "internal_error"
            and response["error_type"] == "ValueError"
            and response["error"] is None
            and response["output"] == ""
            and response["diagnostics"] == "",
        )

        check(
            "internal error response contains no secret text",
            "SECRET-" not in json.dumps(response),
        )

        def interrupt_dispatch(
            request: orchestrator.ExternalSupervisorRequest,
        ) -> int:
            raise KeyboardInterrupt()

        orchestrator.dispatch_external_supervisor_request = (
            interrupt_dispatch
        )

        try:
            orchestrator.execute_external_supervisor_json_request(
                valid_json
            )
        except KeyboardInterrupt:
            interrupt_propagates = True
        else:
            interrupt_propagates = False

        check(
            "KeyboardInterrupt propagates through protocol executor",
            interrupt_propagates,
        )

    finally:
        orchestrator.dispatch_external_supervisor_request = (
            original_dispatch
        )

    original_argv = sys.argv
    original_stdin = sys.stdin
    original_executor = (
        orchestrator.execute_external_supervisor_json_request
    )

    try:
        calls: list[str] = []

        def fake_executor(text: str) -> dict[str, object]:
            calls.append(text)
            return {
                "schema_version": "1.0",
                "request_id": "cli-r0001",
                "disposition": "completed",
                "result": 0,
                "output": "Türkçe αβγ",
                "diagnostics": "",
                "error_type": None,
                "error": None,
            }

        orchestrator.execute_external_supervisor_json_request = (
            fake_executor
        )

        sys.argv = [
            "orchestrator.py",
            "external-request",
        ]
        sys.stdin = StringIO('{"request":"value"}\n')

        stdout = StringIO()
        stderr = StringIO()

        with (
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = orchestrator.main()

        lines = stdout.getvalue().splitlines()

        check(
            "external-request CLI delivers protocol with exit 0",
            exit_code == 0,
        )

        check(
            "external-request CLI forwards stdin unchanged once",
            calls == ['{"request":"value"}\n'],
        )

        check(
            "external-request CLI emits exactly one JSON line",
            len(lines) == 1
            and json.loads(lines[0])["request_id"]
            == "cli-r0001",
        )

        check(
            "external-request CLI preserves Unicode",
            "Türkçe αβγ" in lines[0],
        )

        check(
            "external-request CLI emits no wrapper stderr",
            stderr.getvalue() == "",
        )

        parser = orchestrator.build_parser()
        parser_stderr = StringIO()

        try:
            with redirect_stderr(parser_stderr):
                parser.parse_args(
                    [
                        "external-request",
                        "--command",
                        "human-approval",
                    ]
                )
        except SystemExit as exc:
            passthrough_rejected = exc.code == 2
        else:
            passthrough_rejected = False

        check(
            "external-request rejects command passthrough",
            passthrough_rejected,
        )

    finally:
        sys.argv = original_argv
        sys.stdin = original_stdin
        orchestrator.execute_external_supervisor_json_request = (
            original_executor
        )


def main() -> int:
    print("Trusted Hybrid AI Orchestrator Runtime Regression")
    print("N11 deterministic regression harness")
    print("=============================================")

    tests = (
        test_n4_execution_id_allocator,
        test_n4_result_admissibility,
        test_n4_cancellation_resolution,
        test_n5_budget_resolution,
        test_n5_watchdog,
        test_n5_timeout_prediction,
        test_n6_worker_execution_supervisor,
        test_n7_provider_boundary,
        test_n8_lm_studio_adapter,
        test_n9_external_supervisor_boundary,
        test_n10_reference_protocol,
    )

    try:
        for test in tests:
            test()

    except RegressionFailure as exc:
        print(f"\nFAIL: {exc}")
        return 1

    print("\nN4-N10 regression suite passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
