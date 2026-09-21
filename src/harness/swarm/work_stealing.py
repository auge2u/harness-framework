"""Work-stealing queue for load-balanced task distribution."""

from __future__ import annotations

import threading
from collections import deque
from typing import Dict, List, Optional

from harness.swarm.types import SwarmTask


class WorkStealingQueue:
    """Queue that supports work-stealing for load balancing.

    Each worker has its own local deque.  When a worker exhausts its own
    queue it attempts to *steal* a task from the tail of another worker's
    deque.  This is the classic Chase-Lev / Arora-Blumofe-Plaxton style
    work-stealing adapted for Python threading.

    All operations are thread-safe.
    """

    def __init__(self):
        self.queues: Dict[str, deque] = {}  # worker_id -> deque of tasks
        self.locks: Dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()
        self._worker_ids: List[str] = []

    def register_worker(self, worker_id: str) -> None:
        """Register a new worker with an empty queue.

        Args:
            worker_id: Unique identifier for the worker.
        """
        with self._global_lock:
            if worker_id not in self.queues:
                self.queues[worker_id] = deque()
                self.locks[worker_id] = threading.Lock()
                self._worker_ids.append(worker_id)

    def push(self, worker_id: str, task: SwarmTask) -> None:
        """Push a task onto a worker's queue.

        If *worker_id* has not been registered, it is auto-registered.

        Args:
            worker_id: The worker whose queue should receive the task.
            task: The :class:`SwarmTask` to enqueue.
        """
        with self._global_lock:
            if worker_id not in self.queues:
                self.queues[worker_id] = deque()
                self.locks[worker_id] = threading.Lock()
                self._worker_ids.append(worker_id)

        with self.locks[worker_id]:
            self.queues[worker_id].append(task)

    def pop(self, worker_id: str) -> Optional[SwarmTask]:
        """Pop a task from a worker's own queue.

        If the worker's queue is empty, a steal attempt is made from the
        busiest other worker.

        Args:
            worker_id: The worker requesting a task.

        Returns:
            A :class:`SwarmTask` or ``None`` if no work is available.
        """
        # Fast path: own queue
        if worker_id in self.queues:
            with self.locks[worker_id]:
                if self.queues[worker_id]:
                    return self.queues[worker_id].popleft()

        # Slow path: steal from another worker
        return self.steal(worker_id)

    def steal(self, thief_id: str) -> Optional[SwarmTask]:
        """Steal a task from another worker's queue.

        The victim is chosen as the worker with the largest queue length.
        The task is taken from the *tail* of the victim's deque to reduce
        contention with the victim's own head pops.

        Args:
            thief_id: The worker attempting to steal.

        Returns:
            A :class:`SwarmTask` or ``None`` if stealing failed.
        """
        with self._global_lock:
            candidates = [
                (wid, len(self.queues.get(wid, [])))
                for wid in self._worker_ids
                if wid != thief_id
            ]

        if not candidates:
            return None

        # Sort by queue length descending
        candidates.sort(key=lambda x: x[1], reverse=True)

        for victim_id, _ in candidates:
            if victim_id not in self.locks:
                continue
            with self.locks[victim_id]:
                if self.queues.get(victim_id):
                    return self.queues[victim_id].pop()

        return None

    def queue_length(self, worker_id: str) -> int:
        """Return the number of tasks in a worker's queue.

        Args:
            worker_id: The worker to query.

        Returns:
            Queue length, or ``0`` if the worker is not registered.
        """
        if worker_id not in self.queues:
            return 0
        with self.locks[worker_id]:
            return len(self.queues[worker_id])

    def total_tasks(self) -> int:
        """Return the total number of tasks across all queues.

        Returns:
            Sum of all queue lengths.
        """
        total = 0
        with self._global_lock:
            worker_ids = list(self._worker_ids)
        for wid in worker_ids:
            if wid in self.locks:
                with self.locks[wid]:
                    total += len(self.queues.get(wid, []))
        return total

    def all_tasks(self) -> List[SwarmTask]:
        """Return a snapshot of all tasks in all queues.

        Returns:
            Flat list of every :class:`SwarmTask` currently queued.
        """
        tasks: List[SwarmTask] = []
        with self._global_lock:
            worker_ids = list(self._worker_ids)
        for wid in worker_ids:
            if wid in self.locks:
                with self.locks[wid]:
                    tasks.extend(list(self.queues.get(wid, [])))
        return tasks
