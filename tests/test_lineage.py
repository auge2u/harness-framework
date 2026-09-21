"""Tests for harness.analysis.lineage module."""
from __future__ import annotations

import pytest

from harness.analysis.lineage import HarnessLineage, LineageNode
from harness.core.config import HarnessConfig


class TestLineageCommit:
    """Test committing configurations to the lineage."""

    def test_commit_creates_node(self, tmp_path, sample_config):
        lineage = HarnessLineage(str(tmp_path / "lineage"))
        node = lineage.commit(sample_config)
        assert isinstance(node, LineageNode)
        assert node.version
        assert node.parent is None

    def test_commit_chain(self, tmp_path, sample_config):
        lineage = HarnessLineage(str(tmp_path / "lineage"))
        n1 = lineage.commit(sample_config)
        n2 = lineage.commit(sample_config)
        assert n2.parent == n1.version

    def test_get_latest(self, tmp_path, sample_config):
        lineage = HarnessLineage(str(tmp_path / "lineage"))
        assert lineage.get_latest() is None
        node = lineage.commit(sample_config)
        assert lineage.get_latest().version == node.version


class TestLineageHistory:
    """Test history traversal."""

    def test_get_history(self, tmp_path, sample_config):
        lineage = HarnessLineage(str(tmp_path / "lineage"))
        n1 = lineage.commit(sample_config)
        n2 = lineage.commit(sample_config)
        history = lineage.get_history()
        assert len(history) == 2
        assert history[0].version == n1.version
        assert history[1].version == n2.version

    def test_get_node(self, tmp_path, sample_config):
        lineage = HarnessLineage(str(tmp_path / "lineage"))
        node = lineage.commit(sample_config)
        found = lineage.get_node(node.version)
        assert found is not None
        assert found.version == node.version

    def test_get_node_missing(self, tmp_path):
        lineage = HarnessLineage(str(tmp_path / "lineage"))
        assert lineage.get_node("nonexistent") is None


class TestLineageBranch:
    """Test branching and checkout."""

    def test_branch_and_list(self, tmp_path, sample_config):
        lineage = HarnessLineage(str(tmp_path / "lineage"))
        node = lineage.commit(sample_config)
        lineage.branch(node.version, "experiment")
        branches = lineage.list_branches()
        assert branches["experiment"] == node.version

    def test_checkout(self, tmp_path, sample_config):
        lineage = HarnessLineage(str(tmp_path / "lineage"))
        node = lineage.commit(sample_config)
        config = lineage.checkout(node.version)
        assert isinstance(config, HarnessConfig)
        assert config.name == sample_config.name

    def test_checkout_missing_raises(self, tmp_path):
        lineage = HarnessLineage(str(tmp_path / "lineage"))
        with pytest.raises(ValueError):
            lineage.checkout("nonexistent")


class TestLineageDiff:
    """Test diffing between versions."""

    def test_diff_identical(self, tmp_path, sample_config):
        lineage = HarnessLineage(str(tmp_path / "lineage"))
        n1 = lineage.commit(sample_config)
        n2 = lineage.commit(sample_config)
        diff = lineage.diff(n1.version, n2.version)
        assert isinstance(diff, dict)

    def test_diff_missing_raises(self, tmp_path, sample_config):
        lineage = HarnessLineage(str(tmp_path / "lineage"))
        n1 = lineage.commit(sample_config)
        with pytest.raises(ValueError):
            lineage.diff(n1.version, "nonexistent")
