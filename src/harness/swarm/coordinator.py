"""Swarm coordinator — dynamic agent spawning, work-stealing, and consensus."""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Set

from harness.agent_backend import AgentBackend, BackendConfig, BackendResponse, Message, MockBackend
from harness.core.exceptions import BudgetExceededError
from harness.swarm.types import ConsensusReport, SwarmResult, SwarmTask, SwarmWorker
from harness.swarm.work_stealing import WorkStealingQueue


class SwarmCoordinator:
    """Coordinates a swarm of worker agents with dynamic task decomposition.

    Key capabilities:

    - **Dynamic task decomposition:** Break large tasks into parallel subtasks.
    - **Work-stealing:** Idle workers pull tasks from busy workers' queues.
    - **Consensus aggregation:** Combine results with conflict detection.
    - **Cost-aware early termination:** Stop when sufficient consensus reached.

    Args:
        max_workers: Maximum number of worker agents to spawn.
        consensus_threshold: Minimum agreement score (0-1) to trigger early
            termination.
        cost_budget_usd: Total cost budget in US dollars.
        timeout_seconds: Wall-clock timeout for the entire swarm run.
        backend_factory: Callable that returns a fresh :class:`AgentBackend`
            instance for each new worker.  Defaults to :class:`MockBackend`.
    """

    def __init__(
        self,
        max_workers: int = 10,
        consensus_threshold: float = 0.8,
        cost_budget_usd: float = 5.0,
        timeout_seconds: float = 120.0,
        backend_factory: Optional[Callable[[], AgentBackend]] = None,
    ) -> None:
        self.max_workers = max(max_workers, 1)
        self.consensus_threshold = max(0.0, min(1.0, consensus_threshold))
        self.cost_budget = max(0.0, cost_budget_usd)
        self.timeout = max(0.0, timeout_seconds)
        self.backend_factory = backend_factory or (lambda: MockBackend())

        self.workers: List[SwarmWorker] = []
        self.results: Dict[str, SwarmResult] = {}
        self.cost_used: float = 0.0

        self._cancel_event = threading.Event()
        self._results_lock = threading.Lock()
        self._cost_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Task decomposition
    # ------------------------------------------------------------------

    def decompose(self, task: SwarmTask) -> List[SwarmTask]:
        """Decompose a complex task into parallel subtasks.

        For :class:`MockBackend`, rule-based decomposition is used:

        - ``"analyze codebase"`` → structure, dependencies, tests
        - ``"extract entities"`` → per-document extraction tasks
        - ``"synthesize report"`` → per-section synthesis tasks
        - ``"verify solution"`` → per-criterion verification tasks

        Args:
            task: The parent task to decompose.

        Returns:
            List of subtasks.  If no rules match, a single-task list
            containing the original task is returned.
        """
        desc_lower = task.description.lower()

        if "analyze" in desc_lower or "analysis" in desc_lower:
            return self._decompose_analyze(task)
        if "extract" in desc_lower:
            return self._decompose_extract(task)
        if "synthesize" in desc_lower or "synthesis" in desc_lower:
            return self._decompose_synthesize(task)
        if "verify" in desc_lower or "verification" in desc_lower:
            return self._decompose_verify(task)

        # No decomposition rule matches — return the task as-is
        return [task]

    def _decompose_analyze(self, task: SwarmTask) -> List[SwarmTask]:
        bases = ["structure", "dependencies", "tests", "performance"]
        subtasks = []
        for idx, aspect in enumerate(bases):
            sub = SwarmTask(
                task_id=f"{task.task_id}-analyze-{idx}",
                description=f"Analyze {aspect} for: {task.description}",
                task_type="analyze",
                input_data={**task.input_data, "aspect": aspect},
                priority=task.priority,
                estimated_tokens=task.estimated_tokens // len(bases),
            )
            subtasks.append(sub)
        return subtasks

    def _decompose_extract(self, task: SwarmTask) -> List[SwarmTask]:
        docs = task.input_data.get("documents", ["doc1", "doc2", "doc3"])
        subtasks = []
        for idx, doc in enumerate(docs):
            sub = SwarmTask(
                task_id=f"{task.task_id}-extract-{idx}",
                description=f"Extract entities from {doc}",
                task_type="extract",
                input_data={**task.input_data, "document": doc},
                priority=task.priority,
                estimated_tokens=task.estimated_tokens // max(len(docs), 1),
            )
            subtasks.append(sub)
        return subtasks

    def _decompose_synthesize(self, task: SwarmTask) -> List[SwarmTask]:
        sections = task.input_data.get("sections", ["intro", "body", "conclusion"])
        subtasks = []
        for idx, section in enumerate(sections):
            sub = SwarmTask(
                task_id=f"{task.task_id}-synth-{idx}",
                description=f"Synthesize {section} section for: {task.description}",
                task_type="synthesize",
                input_data={**task.input_data, "section": section},
                priority=task.priority,
                estimated_tokens=task.estimated_tokens // max(len(sections), 1),
            )
            subtasks.append(sub)
        return subtasks

    def _decompose_verify(self, task: SwarmTask) -> List[SwarmTask]:
        criteria = task.input_data.get("criteria", ["correctness", "safety", "performance"])
        subtasks = []
        for idx, criterion in enumerate(criteria):
            sub = SwarmTask(
                task_id=f"{task.task_id}-verify-{idx}",
                description=f"Verify {criterion} for: {task.description}",
                task_type="verify",
                input_data={**task.input_data, "criterion": criterion},
                priority=task.priority,
                estimated_tokens=task.estimated_tokens // max(len(criteria), 1),
            )
            subtasks.append(sub)
        return subtasks

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------

    def spawn_worker(self, role: str = "coder") -> SwarmWorker:
        """Spawn a new worker with the given role.

        Args:
            role: One of ``"coder"``, ``"explorer"``, ``"planner"``,
                ``"synthesizer"``, or ``"verifier"``.

        Returns:
            The newly created :class:`SwarmWorker`.
        """
        worker_id = f"worker-{role}-{uuid.uuid4().hex[:8]}"
        backend = self.backend_factory()
        capabilities = self._role_capabilities(role)
        worker = SwarmWorker(
            worker_id=worker_id,
            role=role,
            backend=backend,
            capabilities=capabilities,
        )
        self.workers.append(worker)
        return worker

    @staticmethod
    def _role_capabilities(role: str) -> List[str]:
        mapping: Dict[str, List[str]] = {
            "coder": ["code_generation", "refactoring", "debugging"],
            "explorer": ["search", "discovery", "data_collection"],
            "planner": ["architecture", "design", "scheduling"],
            "synthesizer": ["summarization", "reporting", "integration"],
            "verifier": ["testing", "validation", "audit"],
        }
        return mapping.get(role, ["general"])

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    def assign_task(self, task: SwarmTask, worker: SwarmWorker) -> SwarmResult:
        """Assign a task to a worker and execute it.

        The worker's backend :meth:`complete` method is called with the
        task description.  Cost and latency are tracked.  If the cost
        budget has already been exhausted the task is returned as
        ``timeout`` with a budget-exceeded message.

        Args:
            task: The task to execute.
            worker: The worker that will execute it.

        Returns:
            A :class:`SwarmResult` describing the outcome.
        """
        if self._cancel_event.is_set():
            return SwarmResult(
                task_id=task.task_id,
                worker_id=worker.worker_id,
                status="timeout",
                output={"error": "cancelled"},
                cost=0.0,
                latency_ms=0.0,
                confidence=0.0,
            )

        with self._cost_lock:
            if self.cost_used >= self.cost_budget:
                return SwarmResult(
                    task_id=task.task_id,
                    worker_id=worker.worker_id,
                    status="timeout",
                    output={"error": "budget_exceeded"},
                    cost=0.0,
                    latency_ms=0.0,
                    confidence=0.0,
                )

        start = time.perf_counter()
        try:
            messages = [
                Message(role="system", content=f"You are a {worker.role} agent."),
                Message(role="user", content=task.description),
            ]
            config = BackendConfig(
                provider="mock",
                model="mock",
                max_tokens=task.estimated_tokens,
            )
            response: BackendResponse = worker.backend.complete(messages, config)

            latency_ms = (time.perf_counter() - start) * 1000.0
            cost = response.cost_usd

            with self._cost_lock:
                self.cost_used += cost
            worker.total_cost += cost
            worker.tasks_completed += 1

            # Determine confidence based on response content heuristics
            confidence = self._estimate_confidence(response.content)

            return SwarmResult(
                task_id=task.task_id,
                worker_id=worker.worker_id,
                status="completed",
                output={
                    "content": response.content,
                    "task_type": task.task_type,
                    "role": worker.role,
                },
                cost=cost,
                latency_ms=latency_ms,
                confidence=confidence,
            )

        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000.0
            worker.tasks_failed += 1
            return SwarmResult(
                task_id=task.task_id,
                worker_id=worker.worker_id,
                status="failed",
                output={"error": str(exc), "task_type": task.task_type},
                cost=0.0,
                latency_ms=latency_ms,
                confidence=0.0,
            )

    @staticmethod
    def _estimate_confidence(content: str) -> float:
        """Heuristic confidence estimate from response content."""
        content_lower = content.lower()
        certainty_terms = ["certain", "sure", "confident", "definitely", "clearly"]
        uncertainty_terms = ["uncertain", "maybe", "possibly", "unclear", "unknown"]

        certainty_count = sum(1 for t in certainty_terms if t in content_lower)
        uncertainty_count = sum(1 for t in uncertainty_terms if t in content_lower)

        base = 0.7
        delta = certainty_count * 0.05 - uncertainty_count * 0.05
        return max(0.0, min(1.0, base + delta))

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def run(self, task: SwarmTask) -> ConsensusReport:
        """Execute a task using the swarm.

        Algorithm:

        1. Decompose the task into subtasks.
        2. Spawn workers up to ``max_workers``.
        3. Distribute tasks via a :class:`WorkStealingQueue`.
        4. Execute tasks in parallel respecting dependencies.
        5. Collect results, checking for early consensus after each batch.
        6. If the consensus threshold is reached, cancel remaining work.
        7. Aggregate all results into a :class:`ConsensusReport`.

        Args:
            task: The top-level task to execute.

        Returns:
            A :class:`ConsensusReport` with aggregated results.

        Raises:
            BudgetExceededError: If the cost budget is exceeded.
        """
        self._cancel_event.clear()
        self.results = {}
        self.cost_used = 0.0
        self.workers = []

        # 1. Decompose
        subtasks = self.decompose(task)

        # 2. Spawn workers (one per subtask, capped at max_workers)
        num_workers = min(len(subtasks), self.max_workers)
        roles = ["coder", "explorer", "planner", "synthesizer", "verifier"]
        for i in range(num_workers):
            self.spawn_worker(role=roles[i % len(roles)])

        # 3. Build dependency graph and identify ready tasks
        ready_tasks, pending_tasks = self._resolve_dependencies(subtasks)

        # 4. Distribute ready tasks to workers via work-stealing queue
        ws_queue = WorkStealingQueue()
        for worker in self.workers:
            ws_queue.register_worker(worker.worker_id)

        for t in ready_tasks:
            ws_queue.push(self.workers[0].worker_id, t)

        # 5. Execute in waves (dependency-respecting)
        deadline = time.time() + self.timeout
        completed_task_ids: Set[str] = set()

        while pending_tasks or ws_queue.total_tasks() > 0 or ready_tasks:
            # Check timeout
            if time.time() > deadline:
                self._cancel_event.set()
                break

            # Check budget
            with self._cost_lock:
                if self.cost_used >= self.cost_budget:
                    self._cancel_event.set()
                    raise BudgetExceededError(
                        f"Swarm cost budget exceeded: ${self.cost_used:.4f} "
                        f">= ${self.cost_budget:.4f}"
                    )

            # Gather current ready batch
            current_batch = ready_tasks[:]
            ready_tasks = []

            # If nothing ready but pending exists, we may be waiting on deps
            if not current_batch and not pending_tasks:
                break
            if not current_batch and pending_tasks:
                # Check if any pending became ready
                newly_ready, pending_tasks = self._check_pending_ready(
                    pending_tasks, completed_task_ids
                )
                if newly_ready:
                    ready_tasks.extend(newly_ready)
                    for t in ready_tasks:
                        ws_queue.push(self.workers[0].worker_id, t)
                    continue
                # Deadlock or all done — break
                break

            # Distribute batch to queues
            for idx, t in enumerate(current_batch):
                worker_idx = idx % len(self.workers)
                ws_queue.push(self.workers[worker_idx].worker_id, t)

            # Execute batch in parallel
            self._execute_batch(ws_queue, deadline)

            # Post-batch budget check
            with self._cost_lock:
                if self.cost_used >= self.cost_budget:
                    self._cancel_event.set()
                    raise BudgetExceededError(
                        f"Swarm cost budget exceeded: ${self.cost_used:.4f} "
                        f">= ${self.cost_budget:.4f}"
                    )

            # Collect completed IDs
            with self._results_lock:
                completed_task_ids.update(self.results.keys())

            # Promote pending tasks whose dependencies are now satisfied
            newly_ready, pending_tasks = self._check_pending_ready(
                pending_tasks, completed_task_ids
            )
            ready_tasks.extend(newly_ready)

            # Early consensus check
            with self._results_lock:
                current_results = list(self.results.values())
            if current_results and self._check_early_termination(current_results):
                self._cancel_event.set()
                break

        # 6. Aggregate
        with self._results_lock:
            all_results = list(self.results.values())
        return self.aggregate(all_results)

    def _resolve_dependencies(
        self, tasks: List[SwarmTask]
    ) -> tuple[List[SwarmTask], List[SwarmTask]]:
        """Split tasks into ready (no deps) and pending (has deps)."""
        ready: List[SwarmTask] = []
        pending: List[SwarmTask] = []
        for t in tasks:
            if not t.dependencies:
                ready.append(t)
            else:
                pending.append(t)
        return ready, pending

    def _check_pending_ready(
        self, pending: List[SwarmTask], completed: Set[str]
    ) -> tuple[List[SwarmTask], List[SwarmTask]]:
        """Move pending tasks whose dependencies are all completed to ready."""
        ready: List[SwarmTask] = []
        still_pending: List[SwarmTask] = []
        for t in pending:
            if all(dep in completed for dep in t.dependencies):
                ready.append(t)
            else:
                still_pending.append(t)
        return ready, still_pending

    def _execute_batch(
        self, ws_queue: WorkStealingQueue, deadline: float
    ) -> None:
        """Execute tasks from the work-stealing queue using a thread pool."""
        futures: Dict[Any, SwarmTask] = {}

        with ThreadPoolExecutor(max_workers=len(self.workers)) as pool:
            assigned = 0
            for worker in self.workers:
                if time.time() > deadline or self._cancel_event.is_set():
                    break
                task = ws_queue.pop(worker.worker_id)
                if task is None:
                    continue
                future = pool.submit(self.assign_task, task, worker)
                futures[future] = task
                assigned += 1

            if not futures:
                return

            for future in as_completed(futures):
                if time.time() > deadline:
                    self._cancel_event.set()
                task = futures[future]
                try:
                    result = future.result(timeout=max(0, deadline - time.time()))
                except Exception as exc:
                    result = SwarmResult(
                        task_id=task.task_id,
                        worker_id="unknown",
                        status="failed",
                        output={"error": str(exc)},
                        cost=0.0,
                        latency_ms=0.0,
                        confidence=0.0,
                    )
                with self._results_lock:
                    self.results[task.task_id] = result

    # ------------------------------------------------------------------
    # Consensus
    # ------------------------------------------------------------------

    def aggregate(self, results: List[SwarmResult]) -> ConsensusReport:
        """Aggregate results with consensus detection.

        For structured outputs the ``content`` field is compared.
        For text outputs a simple term-overlap similarity is used.
        Completed results that disagree with the majority cluster are marked
        as dissenting.  Failed / timed-out results are excluded from both
        consensus and dissent calculations.

        Args:
            results: All :class:`SwarmResult` objects to aggregate.

        Returns:
            A :class:`ConsensusReport`.
        """
        if not results:
            return ConsensusReport(
                results=[],
                agreement_score=0.0,
                consensus_output={},
                dissenting_views=[],
                confidence=0.0,
            )

        completed = [r for r in results if r.status == "completed"]

        # Group by output content similarity
        clusters = self._cluster_results(results)

        if not clusters:
            # Nothing completed successfully
            return ConsensusReport(
                results=results,
                agreement_score=0.0,
                consensus_output={},
                dissenting_views=[],
                confidence=0.0,
            )

        # Pick the largest cluster as consensus
        best_cluster = max(clusters, key=lambda c: len(c))
        agreement_score = len(best_cluster) / len(completed) if completed else 0.0

        # Build consensus output from best cluster
        consensus_output = self._build_consensus_output(best_cluster)

        # Identify dissenting views (only among completed results)
        dissenting: List[SwarmResult] = []
        for r in completed:
            if r not in best_cluster:
                dissenting.append(r)

        # Overall confidence = weighted average of confidences in best cluster
        avg_confidence = sum(r.confidence for r in best_cluster) / len(best_cluster)

        return ConsensusReport(
            results=results,
            agreement_score=agreement_score,
            consensus_output=consensus_output,
            dissenting_views=dissenting,
            confidence=avg_confidence * agreement_score,
        )

    def _cluster_results(self, results: List[SwarmResult]) -> List[List[SwarmResult]]:
        """Cluster *completed* results by content similarity.

        Failed and timed-out results are excluded from clustering.

        Returns:
            List of clusters, each a list of similar results.
        """
        clusters: List[List[SwarmResult]] = []
        for r in results:
            if r.status != "completed":
                continue
            placed = False
            for cluster in clusters:
                if self._results_similar(r, cluster[0]):
                    cluster.append(r)
                    placed = True
                    break
            if not placed:
                clusters.append([r])
        return clusters

    def _results_similar(self, a: SwarmResult, b: SwarmResult) -> bool:
        """Check whether two results are similar enough to cluster together."""
        content_a = str(a.output.get("content", ""))
        content_b = str(b.output.get("content", ""))

        if not content_a or not content_b:
            return a.status == b.status

        # Simple term overlap similarity
        terms_a = set(content_a.lower().split())
        terms_b = set(content_b.lower().split())
        if not terms_a or not terms_b:
            return content_a == content_b

        intersection = terms_a & terms_b
        union = terms_a | terms_b
        similarity = len(intersection) / len(union) if union else 0.0
        return similarity >= 0.5

    @staticmethod
    def _build_consensus_output(cluster: List[SwarmResult]) -> Dict[str, Any]:
        """Build a merged consensus output from a cluster of results."""
        if not cluster:
            return {}

        # Use the most common content as the consensus
        contents: List[str] = []
        for r in cluster:
            content = r.output.get("content", "")
            if content:
                contents.append(content)

        if not contents:
            return {"status": "no_content"}

        # Simple frequency-based selection
        from collections import Counter

        counter = Counter(contents)
        most_common = counter.most_common(1)[0][0]

        return {
            "content": most_common,
            "contributors": len(cluster),
            "avg_confidence": sum(r.confidence for r in cluster) / len(cluster),
        }

    def _check_early_termination(self, results: List[SwarmResult]) -> bool:
        """Check if we have enough consensus to stop early.

        Args:
            results: Currently collected results.

        Returns:
            ``True`` if the consensus threshold has been met.
        """
        if not results:
            return False

        completed = [r for r in results if r.status == "completed"]
        if not completed:
            return False

        clusters = self._cluster_results(completed)
        if not clusters:
            return False

        best_cluster = max(clusters, key=lambda c: len(c))
        agreement = len(best_cluster) / len(completed)
        return agreement >= self.consensus_threshold
