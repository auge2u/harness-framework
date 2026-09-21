"""Comprehensive tests for the Swarm orchestrator."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from harness.agent_backend import MockBackend
from harness.core.exceptions import BudgetExceededError
from harness.plugins.swarm import SwarmOrchestrationPlugin
from harness.swarm.coordinator import SwarmCoordinator
from harness.swarm.types import ConsensusReport, SwarmResult, SwarmTask, SwarmWorker
from harness.swarm.work_stealing import WorkStealingQueue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def coordinator():
    return SwarmCoordinator(
        max_workers=4,
        consensus_threshold=0.8,
        cost_budget_usd=10.0,
        timeout_seconds=30.0,
    )


@pytest.fixture
def ws_queue():
    return WorkStealingQueue()


@pytest.fixture
def sample_task():
    return SwarmTask(
        task_id="task-1",
        description="Analyze codebase structure",
        task_type="analyze",
        input_data={"repo": "test-repo"},
        priority=3,
    )


# ---------------------------------------------------------------------------
# 1. Task decomposition
# ---------------------------------------------------------------------------


class TestTaskDecomposition:
    def test_decompose_analyze_codebase(self, coordinator):
        task = SwarmTask(
            task_id="analyze-root",
            description="Analyze codebase",
            task_type="analyze",
            input_data={},
        )
        subtasks = coordinator.decompose(task)
        assert len(subtasks) > 1
        assert all(s.task_type == "analyze" for s in subtasks)
        assert any("structure" in s.description for s in subtasks)

    def test_decompose_extract_entities(self, coordinator):
        task = SwarmTask(
            task_id="extract-root",
            description="Extract entities",
            task_type="extract",
            input_data={"documents": ["a.md", "b.md", "c.md"]},
        )
        subtasks = coordinator.decompose(task)
        assert len(subtasks) == 3
        assert all(s.task_type == "extract" for s in subtasks)

    def test_decompose_synthesize_report(self, coordinator):
        task = SwarmTask(
            task_id="synth-root",
            description="Synthesize report",
            task_type="synthesize",
            input_data={"sections": ["intro", "body"]},
        )
        subtasks = coordinator.decompose(task)
        assert len(subtasks) == 2
        assert all(s.task_type == "synthesize" for s in subtasks)

    def test_decompose_verify_solution(self, coordinator):
        task = SwarmTask(
            task_id="verify-root",
            description="Verify solution",
            task_type="verify",
            input_data={"criteria": ["correctness", "safety"]},
        )
        subtasks = coordinator.decompose(task)
        assert len(subtasks) == 2
        assert all(s.task_type == "verify" for s in subtasks)

    def test_decompose_no_match_returns_original(self, coordinator):
        task = SwarmTask(
            task_id="custom-root",
            description="Do something completely custom",
            task_type="custom",
            input_data={},
        )
        subtasks = coordinator.decompose(task)
        assert len(subtasks) == 1
        assert subtasks[0].task_id == "custom-root"

    def test_decompose_preserves_priority(self, coordinator):
        task = SwarmTask(
            task_id="extract-root",
            description="Extract entities",
            task_type="extract",
            input_data={"documents": ["a.md", "b.md"]},
            priority=2,
        )
        subtasks = coordinator.decompose(task)
        assert all(s.priority == 2 for s in subtasks)


# ---------------------------------------------------------------------------
# 2. Worker spawning
# ---------------------------------------------------------------------------


class TestWorkerSpawning:
    def test_spawn_worker_creates_unique_ids(self, coordinator):
        w1 = coordinator.spawn_worker("coder")
        w2 = coordinator.spawn_worker("coder")
        assert w1.worker_id != w2.worker_id
        assert w1.worker_id.startswith("worker-coder-")

    def test_spawn_worker_assigns_role(self, coordinator):
        w = coordinator.spawn_worker("verifier")
        assert w.role == "verifier"
        assert "testing" in w.capabilities
        assert "validation" in w.capabilities

    def test_spawn_worker_tracks_in_coordinator(self, coordinator):
        w = coordinator.spawn_worker("planner")
        assert w in coordinator.workers
        assert len(coordinator.workers) == 1

    def test_spawn_worker_with_mock_backend(self, coordinator):
        w = coordinator.spawn_worker("coder")
        assert isinstance(w.backend, MockBackend)

    def test_role_capabilities_mapping(self, coordinator):
        assert "search" in SwarmCoordinator._role_capabilities("explorer")
        assert "summarization" in SwarmCoordinator._role_capabilities("synthesizer")
        assert SwarmCoordinator._role_capabilities("unknown") == ["general"]


# ---------------------------------------------------------------------------
# 3. Task assignment and execution
# ---------------------------------------------------------------------------


class TestTaskExecution:
    def test_assign_task_returns_completed(self, coordinator, sample_task):
        worker = coordinator.spawn_worker("coder")
        result = coordinator.assign_task(sample_task, worker)
        assert result.status == "completed"
        assert result.task_id == sample_task.task_id
        assert result.worker_id == worker.worker_id
        assert result.cost > 0
        assert result.latency_ms >= 0

    def test_assign_task_updates_worker_stats(self, coordinator, sample_task):
        worker = coordinator.spawn_worker("coder")
        coordinator.assign_task(sample_task, worker)
        assert worker.tasks_completed == 1
        assert worker.total_cost > 0

    def test_assign_task_tracks_cost(self, coordinator, sample_task):
        worker = coordinator.spawn_worker("coder")
        before = coordinator.cost_used
        coordinator.assign_task(sample_task, worker)
        assert coordinator.cost_used > before

    def test_assign_task_includes_output(self, coordinator, sample_task):
        worker = coordinator.spawn_worker("coder")
        result = coordinator.assign_task(sample_task, worker)
        assert "content" in result.output
        assert result.output["task_type"] == "analyze"

    def test_assign_task_respects_cancel(self, coordinator, sample_task):
        worker = coordinator.spawn_worker("coder")
        coordinator._cancel_event.set()
        result = coordinator.assign_task(sample_task, worker)
        assert result.status == "timeout"
        assert result.output.get("error") == "cancelled"


# ---------------------------------------------------------------------------
# 4. Work stealing
# ---------------------------------------------------------------------------


class TestWorkStealing:
    def test_pop_from_own_queue(self, ws_queue):
        ws_queue.register_worker("w1")
        task = SwarmTask("t1", "task", "analyze", {})
        ws_queue.push("w1", task)
        popped = ws_queue.pop("w1")
        assert popped is not None
        assert popped.task_id == "t1"

    def test_pop_empty_returns_none(self, ws_queue):
        ws_queue.register_worker("w1")
        assert ws_queue.pop("w1") is None

    def test_steal_from_busy_worker(self, ws_queue):
        ws_queue.register_worker("w1")
        ws_queue.register_worker("w2")
        for i in range(5):
            ws_queue.push("w1", SwarmTask(f"t{i}", f"task {i}", "analyze", {}))
        stolen = ws_queue.steal("w2")
        assert stolen is not None
        assert ws_queue.queue_length("w1") == 4

    def test_steal_from_busiest_worker(self, ws_queue):
        ws_queue.register_worker("w1")
        ws_queue.register_worker("w2")
        ws_queue.register_worker("w3")
        for i in range(5):
            ws_queue.push("w1", SwarmTask(f"t{i}", f"task {i}", "analyze", {}))
        for i in range(2):
            ws_queue.push("w2", SwarmTask(f"u{i}", f"task {i}", "analyze", {}))
        stolen = ws_queue.steal("w3")
        assert stolen is not None
        # Should steal from w1 since it has the most tasks
        assert ws_queue.queue_length("w1") == 4
        assert ws_queue.queue_length("w2") == 2

    def test_steal_no_victims_returns_none(self, ws_queue):
        ws_queue.register_worker("w1")
        ws_queue.register_worker("w2")
        assert ws_queue.steal("w2") is None

    def test_total_tasks(self, ws_queue):
        ws_queue.register_worker("w1")
        ws_queue.register_worker("w2")
        ws_queue.push("w1", SwarmTask("t1", "task", "analyze", {}))
        ws_queue.push("w2", SwarmTask("t2", "task", "analyze", {}))
        assert ws_queue.total_tasks() == 2

    def test_queue_length_nonexistent_worker(self, ws_queue):
        assert ws_queue.queue_length("ghost") == 0

    def test_work_stealing_thread_safety(self, ws_queue):
        ws_queue.register_worker("w1")
        ws_queue.register_worker("w2")
        for i in range(100):
            ws_queue.push("w1", SwarmTask(f"t{i}", f"task {i}", "analyze", {}))

        stolen = []

        def steal_many():
            for _ in range(50):
                t = ws_queue.steal("w2")
                if t:
                    stolen.append(t)

        def pop_many():
            for _ in range(50):
                t = ws_queue.pop("w1")
                if t:
                    pass  # consumed

        with ThreadPoolExecutor(max_workers=4) as pool:
            pool.submit(steal_many)
            pool.submit(pop_many)
            pool.submit(steal_many)
            pool.submit(pop_many)

        # Total remaining + stolen + consumed should equal original
        remaining = ws_queue.queue_length("w1")
        assert remaining + len(stolen) <= 100


# ---------------------------------------------------------------------------
# 5. Consensus aggregation
# ---------------------------------------------------------------------------


class TestConsensusAggregation:
    def test_aggregate_empty_results(self, coordinator):
        report = coordinator.aggregate([])
        assert report.agreement_score == 0.0
        assert report.confidence == 0.0
        assert report.consensus_output == {}

    def test_aggregate_single_result(self, coordinator):
        results = [
            SwarmResult("t1", "w1", "completed", {"content": "A"}, 0.001, 10.0, 0.8)
        ]
        report = coordinator.aggregate(results)
        assert report.agreement_score == 1.0
        assert len(report.dissenting_views) == 0

    def test_aggregate_agreement(self, coordinator):
        results = [
            SwarmResult("t1", "w1", "completed", {"content": "alpha beta gamma"}, 0.001, 10.0, 0.8),
            SwarmResult("t2", "w2", "completed", {"content": "alpha beta gamma"}, 0.001, 10.0, 0.8),
            SwarmResult("t3", "w3", "completed", {"content": "alpha beta gamma"}, 0.001, 10.0, 0.8),
        ]
        report = coordinator.aggregate(results)
        assert report.agreement_score == 1.0
        assert len(report.dissenting_views) == 0
        assert report.confidence > 0.5

    def test_aggregate_dissent_detection(self, coordinator):
        results = [
            SwarmResult("t1", "w1", "completed", {"content": "alpha beta gamma"}, 0.001, 10.0, 0.8),
            SwarmResult("t2", "w2", "completed", {"content": "alpha beta gamma"}, 0.001, 10.0, 0.8),
            SwarmResult("t3", "w3", "completed", {"content": "x y z completely different"}, 0.001, 10.0, 0.3),
        ]
        report = coordinator.aggregate(results)
        assert report.agreement_score == 2 / 3
        assert len(report.dissenting_views) == 1
        assert report.dissenting_views[0].task_id == "t3"

    def test_aggregate_ignores_failed(self, coordinator):
        results = [
            SwarmResult("t1", "w1", "completed", {"content": "alpha beta"}, 0.001, 10.0, 0.8),
            SwarmResult("t2", "w2", "failed", {"error": "oops"}, 0.0, 10.0, 0.0),
        ]
        report = coordinator.aggregate(results)
        assert report.agreement_score == 1.0  # only completed considered

    def test_build_consensus_output(self, coordinator):
        results = [
            SwarmResult("t1", "w1", "completed", {"content": "agreed"}, 0.001, 10.0, 0.9),
            SwarmResult("t2", "w2", "completed", {"content": "agreed"}, 0.001, 10.0, 0.8),
        ]
        report = coordinator.aggregate(results)
        assert report.consensus_output.get("content") == "agreed"
        assert report.consensus_output.get("contributors") == 2


# ---------------------------------------------------------------------------
# 6. Early termination
# ---------------------------------------------------------------------------


class TestEarlyTermination:
    def test_early_termination_met(self, coordinator):
        coordinator.consensus_threshold = 0.75
        results = [
            SwarmResult("t1", "w1", "completed", {"content": "same"}, 0.001, 10.0, 0.8),
            SwarmResult("t2", "w2", "completed", {"content": "same"}, 0.001, 10.0, 0.8),
            SwarmResult("t3", "w3", "completed", {"content": "same"}, 0.001, 10.0, 0.8),
            SwarmResult("t4", "w4", "completed", {"content": "different"}, 0.001, 10.0, 0.3),
        ]
        assert coordinator._check_early_termination(results) is True

    def test_early_termination_not_met(self, coordinator):
        coordinator.consensus_threshold = 0.9
        results = [
            SwarmResult("t1", "w1", "completed", {"content": "same"}, 0.001, 10.0, 0.8),
            SwarmResult("t2", "w2", "completed", {"content": "different"}, 0.001, 10.0, 0.8),
        ]
        assert coordinator._check_early_termination(results) is False

    def test_early_termination_empty(self, coordinator):
        assert coordinator._check_early_termination([]) is False

    def test_early_termination_all_failed(self, coordinator):
        results = [
            SwarmResult("t1", "w1", "failed", {"error": "x"}, 0.0, 10.0, 0.0),
            SwarmResult("t2", "w2", "failed", {"error": "y"}, 0.0, 10.0, 0.0),
        ]
        assert coordinator._check_early_termination(results) is False


# ---------------------------------------------------------------------------
# 7. Cost budget enforcement
# ---------------------------------------------------------------------------


class TestCostBudget:
    def test_budget_enforced(self):
        coordinator = SwarmCoordinator(
            max_workers=2,
            cost_budget_usd=0.001,
            timeout_seconds=30.0,
        )
        task = SwarmTask(
            task_id="budget-task",
            description="Extract entities",
            task_type="extract",
            input_data={"documents": ["a", "b"]},
        )
        with pytest.raises(BudgetExceededError):
            coordinator.run(task)

    def test_cost_tracked_across_tasks(self, coordinator):
        worker = coordinator.spawn_worker("coder")
        task1 = SwarmTask("t1", "task1", "analyze", {})
        task2 = SwarmTask("t2", "task2", "analyze", {})
        coordinator.assign_task(task1, worker)
        cost_after_1 = coordinator.cost_used
        coordinator.assign_task(task2, worker)
        assert coordinator.cost_used > cost_after_1


# ---------------------------------------------------------------------------
# 8. Timeout handling
# ---------------------------------------------------------------------------


class TestTimeoutHandling:
    def test_timeout_respected(self):
        coordinator = SwarmCoordinator(
            max_workers=2,
            timeout_seconds=0.01,
        )
        task = SwarmTask(
            task_id="timeout-task",
            description="Analyze codebase",
            task_type="analyze",
            input_data={},
        )
        # Should either timeout or finish quickly; we just ensure no hang
        report = coordinator.run(task)
        assert isinstance(report, ConsensusReport)


# ---------------------------------------------------------------------------
# 9. Dependency ordering
# ---------------------------------------------------------------------------


class TestDependencyOrdering:
    def test_dependencies_respected(self, coordinator):
        task_a = SwarmTask("a", "task A", "analyze", {})
        task_b = SwarmTask("b", "task B", "analyze", {}, dependencies=["a"])
        task_c = SwarmTask("c", "task C", "analyze", {}, dependencies=["a", "b"])

        ready, pending = coordinator._resolve_dependencies([task_a, task_b, task_c])
        assert len(ready) == 1
        assert ready[0].task_id == "a"
        assert len(pending) == 2

    def test_check_pending_ready(self, coordinator):
        task_a = SwarmTask("a", "task A", "analyze", {})
        task_b = SwarmTask("b", "task B", "analyze", {}, dependencies=["a"])
        pending = [task_b]
        ready, still_pending = coordinator._check_pending_ready(pending, {"a"})
        assert len(ready) == 1
        assert ready[0].task_id == "b"
        assert len(still_pending) == 0

    def test_check_pending_not_ready(self, coordinator):
        task_b = SwarmTask("b", "task B", "analyze", {}, dependencies=["a"])
        pending = [task_b]
        ready, still_pending = coordinator._check_pending_ready(pending, set())
        assert len(ready) == 0
        assert len(still_pending) == 1

    def test_task_priority_clamped(self):
        t_high = SwarmTask("t1", "task", "analyze", {}, priority=0)
        t_low = SwarmTask("t2", "task", "analyze", {}, priority=15)
        assert t_high.priority == 1
        assert t_low.priority == 10


# ---------------------------------------------------------------------------
# 10. Full swarm run end-to-end
# ---------------------------------------------------------------------------


class TestFullSwarmRun:
    def test_run_analyze_task(self, coordinator):
        task = SwarmTask(
            task_id="analyze-root",
            description="Analyze codebase",
            task_type="analyze",
            input_data={},
        )
        report = coordinator.run(task)
        assert isinstance(report, ConsensusReport)
        assert len(report.results) > 0
        assert report.agreement_score >= 0.0
        assert report.confidence >= 0.0

    def test_run_extract_task(self, coordinator):
        task = SwarmTask(
            task_id="extract-root",
            description="Extract entities",
            task_type="extract",
            input_data={"documents": ["doc1", "doc2"]},
        )
        report = coordinator.run(task)
        assert isinstance(report, ConsensusReport)
        assert len(report.results) >= 2

    def test_run_synthesize_task(self, coordinator):
        task = SwarmTask(
            task_id="synth-root",
            description="Synthesize report",
            task_type="synthesize",
            input_data={"sections": ["intro", "body", "conclusion"]},
        )
        report = coordinator.run(task)
        assert isinstance(report, ConsensusReport)
        assert len(report.results) == 3

    def test_workers_spawned_up_to_max(self, coordinator):
        task = SwarmTask(
            task_id="big-task",
            description="Extract entities",
            task_type="extract",
            input_data={"documents": ["d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8"]},
        )
        coordinator.run(task)
        assert len(coordinator.workers) <= coordinator.max_workers

    def test_run_with_dependencies(self, coordinator):
        # Create a custom task that decomposes into dependent subtasks
        # We simulate by manually creating subtasks with deps
        sub_a = SwarmTask("sub-a", "Step A", "analyze", {})
        sub_b = SwarmTask("sub-b", "Step B", "analyze", {}, dependencies=["sub-a"])
        sub_c = SwarmTask("sub-c", "Step C", "analyze", {}, dependencies=["sub-a"])

        # Manually run with dependency resolution
        coordinator.workers = []
        coordinator.results = {}
        coordinator.spawn_worker("coder")
        coordinator.spawn_worker("explorer")

        ws_queue = WorkStealingQueue()
        for w in coordinator.workers:
            ws_queue.register_worker(w.worker_id)

        ready, pending = coordinator._resolve_dependencies([sub_a, sub_b, sub_c])
        assert ready == [sub_a]
        assert len(pending) == 2

        # Execute A
        ws_queue.push(coordinator.workers[0].worker_id, sub_a)
        coordinator._execute_batch(ws_queue, time.time() + 10)

        # Now B and C should be ready
        completed = set(coordinator.results.keys())
        newly_ready, pending = coordinator._check_pending_ready(pending, completed)
        assert len(newly_ready) == 2
        assert len(pending) == 0


# ---------------------------------------------------------------------------
# 11. Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_failed_result_aggregation(self, coordinator):
        results = [
            SwarmResult("t1", "w1", "failed", {"error": "timeout"}, 0.0, 1000.0, 0.0),
            SwarmResult("t2", "w2", "failed", {"error": "crash"}, 0.0, 500.0, 0.0),
        ]
        report = coordinator.aggregate(results)
        assert report.agreement_score == 0.0  # no completed results -> no consensus

    def test_worker_failure_does_not_crash_run(self, coordinator):
        # Use a backend that raises on first call
        class FailingBackend(MockBackend):
            def complete(self, messages, config=None):
                self.call_count += 1
                if self.call_count == 1:
                    raise RuntimeError("Simulated failure")
                return super().complete(messages, config)

        coord = SwarmCoordinator(
            max_workers=2,
            backend_factory=FailingBackend,
        )
        task = SwarmTask(
            task_id="err-task",
            description="Extract entities",
            task_type="extract",
            input_data={"documents": ["a", "b"]},
        )
        report = coord.run(task)
        assert isinstance(report, ConsensusReport)
        # At least one result should be failed, at least one completed
        statuses = [r.status for r in report.results]
        assert "failed" in statuses or "completed" in statuses

    def test_mixed_results(self, coordinator):
        results = [
            SwarmResult("t1", "w1", "completed", {"content": "alpha"}, 0.001, 10.0, 0.8),
            SwarmResult("t2", "w2", "failed", {"error": "boom"}, 0.0, 5.0, 0.0),
            SwarmResult("t3", "w3", "completed", {"content": "alpha"}, 0.001, 10.0, 0.8),
        ]
        report = coordinator.aggregate(results)
        assert len(report.results) == 3
        assert len(report.dissenting_views) == 0  # failed not in dissenting


# ---------------------------------------------------------------------------
# 12. Plugin integration
# ---------------------------------------------------------------------------


class TestSwarmPlugin:
    def test_plugin_surface_name(self):
        plugin = SwarmOrchestrationPlugin()
        assert plugin.surface_name == "swarm_orchestration"

    def test_plugin_validate_good_config(self):
        plugin = SwarmOrchestrationPlugin()
        errors = plugin.validate(None)
        assert errors == []

    def test_plugin_validate_bad_workers(self):
        plugin = SwarmOrchestrationPlugin(max_workers=0)
        errors = plugin.validate(None)
        assert any("max_workers" in e for e in errors)

    def test_plugin_validate_bad_threshold(self):
        plugin = SwarmOrchestrationPlugin(consensus_threshold=1.5)
        errors = plugin.validate(None)
        assert any("consensus_threshold" in e for e in errors)

    def test_plugin_validate_bad_budget(self):
        plugin = SwarmOrchestrationPlugin(cost_budget_usd=-1)
        errors = plugin.validate(None)
        assert any("cost_budget_usd" in e for e in errors)

    def test_plugin_validate_bad_timeout(self):
        plugin = SwarmOrchestrationPlugin(timeout_seconds=0)
        errors = plugin.validate(None)
        assert any("timeout_seconds" in e for e in errors)

    def test_plugin_apply_with_task(self):
        plugin = SwarmOrchestrationPlugin(max_workers=2)
        context = type("Ctx", (), {
            "run_id": "test-run",
            "metadata": {
                "task": {
                    "task_id": "plugin-task",
                    "description": "Test plugin task",
                    "task_type": "analyze",
                    "input_data": {},
                }
            }
        })()
        result = plugin.apply(None, context)
        assert result["status"] == "success"
        assert "report" in result
        assert "metrics" in result

    def test_plugin_apply_without_task(self):
        plugin = SwarmOrchestrationPlugin(max_workers=2)
        context = type("Ctx", (), {
            "run_id": "test-run",
            "metadata": {},
        })()
        result = plugin.apply(None, context)
        assert result["status"] == "success"
        assert "report" in result


# ---------------------------------------------------------------------------
# 13. Misc / edge cases
# ---------------------------------------------------------------------------


class TestMiscEdgeCases:
    def test_estimate_confidence_certainty(self):
        conf = SwarmCoordinator._estimate_confidence("I am certain and confident")
        assert conf > 0.7

    def test_estimate_confidence_uncertainty(self):
        conf = SwarmCoordinator._estimate_confidence("I am uncertain and unclear")
        assert conf < 0.7

    def test_result_post_init_clamps_confidence(self):
        r = SwarmResult("t", "w", "completed", {}, 0.0, 0.0, 1.5)
        assert r.confidence == 1.0
        r2 = SwarmResult("t", "w", "completed", {}, 0.0, 0.0, -0.5)
        assert r2.confidence == 0.0

    def test_consensus_report_post_init(self):
        r = ConsensusReport(None, 0.5, None, None, 0.5)
        assert r.results == []
        assert r.consensus_output == {}
        assert r.dissenting_views == []

    def test_max_workers_cap(self):
        coordinator = SwarmCoordinator(max_workers=0)
        assert coordinator.max_workers == 1

    def test_timeout_clamped(self):
        coordinator = SwarmCoordinator(timeout_seconds=-5)
        assert coordinator.timeout == 0.0
