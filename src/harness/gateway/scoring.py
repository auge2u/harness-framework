"""Access scoring algorithms for the DataGateway."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class AccessScore:
    """Access score for a data source surface."""
    surface: str
    score: float  # 0-1
    rate_limit_remaining: int
    cost_per_call_usd: float
    risk_level: str = "medium"  # "low" | "medium" | "high"

    def __post_init__(self):
        if self.score >= 0.7:
            self.risk_level = "low"
        elif self.score >= 0.4:
            self.risk_level = "medium"
        else:
            self.risk_level = "high"


def compute_access_score(source_config: dict, usage_history: list) -> float:
    """Compute access score (0-1) based on recency, failure rate, and cost efficiency.

    Factors:
    - Recency: weighted average with exponential decay (higher weight for recent)
    - Failure rate: 1.0 - (failures / total)
    - Cost efficiency: 1.0 normalized against max expected cost
    - Weights: recency 0.35, failure_rate 0.35, cost_efficiency 0.30
    """
    recency_score = _compute_recency_score(usage_history)
    failure_score = _compute_failure_score(usage_history)
    cost_score = _compute_cost_efficiency(source_config, usage_history)
    return (0.35 * recency_score + 0.35 * failure_score + 0.30 * cost_score)


def _compute_recency_score(usage_history: list) -> float:
    """Compute recency score using exponential decay weighting."""
    if not usage_history:
        return 0.5
    now = time.time()
    total_weight = 0.0
    weighted_sum = 0.0
    for entry in usage_history:
        if isinstance(entry, dict):
            timestamp = entry.get("timestamp", now)
            success = entry.get("success", True)
        else:
            timestamp = now
            success = bool(entry)
        age_seconds = max(0, now - timestamp)
        half_life = 3600.0
        weight = math.exp(-age_seconds / half_life)
        weighted_sum += weight * (1.0 if success else 0.0)
        total_weight += weight
    return weighted_sum / total_weight if total_weight > 0 else 0.5


def _compute_failure_score(usage_history: list) -> float:
    """Compute failure rate score: 1.0 - (failures / total)."""
    if not usage_history:
        return 0.8
    failures = 0
    total = 0
    for entry in usage_history:
        total += 1
        if isinstance(entry, dict) and not entry.get("success", True):
            failures += 1
    return 1.0 - (failures / total) if total > 0 else 0.8


def _compute_cost_efficiency(source_config: dict, usage_history: list) -> float:
    """Compute cost efficiency score normalized against expected max cost."""
    cost_per_call = source_config.get("cost_per_call", 0.0)
    if cost_per_call <= 0:
        return 1.0
    max_expected_cost = source_config.get("max_expected_cost", 1.0)
    if max_expected_cost <= 0:
        max_expected_cost = 1.0
    efficiency = 1.0 - min(cost_per_call / max_expected_cost, 1.0)
    return max(efficiency, 0.0)
