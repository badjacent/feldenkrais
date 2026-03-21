"""
Tests for the pipeline DAG engine.

Covers dependency resolution, execution ordering, and error handling.
"""

import sys
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


@pytest.fixture
def engine_module():
    """Import the pipeline engine module."""
    kb_dir = str(Path(__file__).resolve().parent.parent.parent / "src" / "kb")
    if kb_dir not in sys.path:
        sys.path.insert(0, kb_dir)
    pipeline_dir = str(Path(__file__).resolve().parent.parent.parent / "src" / "kb" / "pipeline")
    if pipeline_dir not in sys.path:
        sys.path.insert(0, pipeline_dir)
    from kb.pipeline.engine import PipelineGraph, NodeKind, NodeStatus, NodeState, PipelineExecutor, PipelineStateStore
    return {
        "PipelineGraph": PipelineGraph,
        "NodeKind": NodeKind,
        "NodeStatus": NodeStatus,
        "NodeState": NodeState,
        "PipelineExecutor": PipelineExecutor,
        "PipelineStateStore": PipelineStateStore,
    }


@pytest.fixture
def simple_graph(engine_module):
    """Create a simple A -> B -> C graph."""
    PipelineGraph = engine_module["PipelineGraph"]
    graph = PipelineGraph()
    graph.add_source("A", hash_fn=lambda: "hash_a", description="Source A")
    graph.add_transform("B", input_names=["A"], transform=lambda A: "result_b", description="Transform B")
    graph.add_transform("C", input_names=["B"], transform=lambda B: "result_c", description="Transform C")
    return graph


class TestDependencyResolution:
    def test_topological_sort(self, engine_module, simple_graph):
        """Should execute nodes in dependency order."""
        order = simple_graph.topological_order()
        assert order.index("A") < order.index("B")
        assert order.index("B") < order.index("C")

    def test_detects_cycles(self, engine_module):
        """Should raise error for circular dependencies."""
        PipelineGraph = engine_module["PipelineGraph"]
        graph = PipelineGraph()
        graph.add_source("A", hash_fn=lambda: "hash_a")
        graph.add_transform("B", input_names=["A"], transform=lambda A: "b")

        # Manually create a cycle by editing the node spec
        graph.nodes["A"].input_names = ["B"]
        graph.nodes["A"].kind = engine_module["NodeKind"].TRANSFORM
        graph._topo_cache = None  # Clear cache

        with pytest.raises(ValueError, match="Cycle detected"):
            graph.topological_order()

    def test_independent_nodes_can_run(self, engine_module):
        """Nodes with no dependencies should be immediately runnable."""
        PipelineGraph = engine_module["PipelineGraph"]
        graph = PipelineGraph()
        graph.add_source("X", hash_fn=lambda: "hash_x")
        graph.add_source("Y", hash_fn=lambda: "hash_y")
        graph.add_transform("Z", input_names=["X", "Y"], transform=lambda X, Y: "z")

        order = graph.topological_order()
        # X and Y should both come before Z
        assert order.index("X") < order.index("Z")
        assert order.index("Y") < order.index("Z")

    def test_descendants(self, engine_module, simple_graph):
        """Should return all downstream nodes."""
        descendants = simple_graph.descendants("A")
        assert descendants == {"B", "C"}

    def test_ancestors(self, engine_module, simple_graph):
        """Should return all upstream nodes."""
        ancestors = simple_graph.ancestors("C")
        assert ancestors == {"A", "B"}

    def test_duplicate_node_raises(self, engine_module):
        """Should raise error for duplicate node names."""
        PipelineGraph = engine_module["PipelineGraph"]
        graph = PipelineGraph()
        graph.add_source("A", hash_fn=lambda: "hash_a")
        with pytest.raises(ValueError, match="Duplicate node name"):
            graph.add_source("A", hash_fn=lambda: "hash_a2")

    def test_unknown_input_raises(self, engine_module):
        """Should raise error for unknown input node."""
        PipelineGraph = engine_module["PipelineGraph"]
        graph = PipelineGraph()
        graph.add_source("A", hash_fn=lambda: "hash_a")
        with pytest.raises(ValueError, match="Unknown input"):
            graph.add_transform("B", input_names=["nonexistent"], transform=lambda x: "b")


class TestNodeExecution:
    def test_runs_executor_function(self, engine_module, simple_graph):
        """Should run the transform function and record state."""
        NodeState = engine_module["NodeState"]
        NodeStatus = engine_module["NodeStatus"]

        # Create mock store
        mock_store = MagicMock()
        mock_store.load_metadata.return_value = None

        executor = engine_module["PipelineExecutor"](simple_graph, mock_store)

        # Run source node A
        states = {}
        state = executor.run_node("A", states)
        assert state.status == NodeStatus.FRESH
        assert state.output_hash != ""
        mock_store.save.assert_called()

    def test_skips_completed_nodes(self, engine_module, simple_graph):
        """Should not re-run nodes that are already completed.

        The compute_staleness method determines which nodes need running.
        Fresh nodes with matching input_hash are not re-run."""
        NodeState = engine_module["NodeState"]
        NodeStatus = engine_module["NodeStatus"]

        mock_store = MagicMock()
        # Return stored state where A is already fresh
        mock_store.load_all.return_value = {
            "A": NodeState(name="A", status=NodeStatus.FRESH,
                           input_hash="hash_a", output_hash="hash_a"),
            "B": NodeState(name="B", status=NodeStatus.FRESH,
                           input_hash="some_hash", output_hash="output_b"),
            "C": NodeState(name="C", status=NodeStatus.FRESH,
                           input_hash="some_hash", output_hash="output_c"),
        }
        mock_store.load_enabled_flags.return_value = {}

        executor = engine_module["PipelineExecutor"](simple_graph, mock_store)
        states = executor.compute_staleness()

        # All nodes should be fresh (none need running)
        assert states["A"].status == NodeStatus.FRESH

    def test_error_propagation(self, engine_module, simple_graph):
        """Downstream nodes should not run if upstream fails.

        When a node has ERROR status, its output_hash is cleared, causing
        downstream nodes to be marked STALE (they can't get valid input)."""
        NodeState = engine_module["NodeState"]
        NodeStatus = engine_module["NodeStatus"]

        mock_store = MagicMock()
        mock_store.load_all.return_value = {
            "A": NodeState(name="A", status=NodeStatus.FRESH,
                           input_hash="hash_a", output_hash="hash_a"),
            "B": NodeState(name="B", status=NodeStatus.ERROR,
                           input_hash="some_hash", output_hash="",
                           error_message="LLM timeout"),
            "C": NodeState(name="C", status=NodeStatus.FRESH,
                           input_hash="old_hash", output_hash="old_output"),
        }
        mock_store.load_enabled_flags.return_value = {}

        executor = engine_module["PipelineExecutor"](simple_graph, mock_store)
        states = executor.compute_staleness()

        # B should be ERROR, C should be STALE (upstream B has no output)
        assert states["B"].status == NodeStatus.ERROR
        assert states["C"].status == NodeStatus.STALE

    def test_records_completion_state(self, engine_module, simple_graph):
        """Running a transform node should call store.save with FRESH status on success."""
        NodeState = engine_module["NodeState"]
        NodeStatus = engine_module["NodeStatus"]

        mock_store = MagicMock()
        mock_store.load_metadata.return_value = None

        executor = engine_module["PipelineExecutor"](simple_graph, mock_store)

        # First run source A
        states = {}
        state_a = executor.run_node("A", states)
        assert state_a.status == NodeStatus.FRESH

        # Then run transform B
        state_b = executor.run_node("B", states)
        assert state_b.status == NodeStatus.FRESH
        assert state_b.output_hash != ""

        # Verify store.save was called with the result
        save_calls = mock_store.save.call_args_list
        # At least 2 saves for A (initial + final) and B (initial RUNNING + final FRESH)
        assert len(save_calls) >= 3  # A:save, B:RUNNING save, B:FRESH save

    def test_transform_error_records_error_state(self, engine_module):
        """Transform that raises should result in ERROR state."""
        PipelineGraph = engine_module["PipelineGraph"]
        NodeStatus = engine_module["NodeStatus"]

        graph = PipelineGraph()
        graph.add_source("A", hash_fn=lambda: "hash_a")
        graph.add_transform("B", input_names=["A"],
                            transform=lambda A: (_ for _ in ()).throw(RuntimeError("boom")))

        mock_store = MagicMock()
        mock_store.load_metadata.return_value = None

        executor = engine_module["PipelineExecutor"](graph, mock_store)

        states = {}
        executor.run_node("A", states)

        # Now run B which raises
        def bad_transform(**kwargs):
            raise RuntimeError("boom")

        graph.nodes["B"].transform = bad_transform
        state_b = executor.run_node("B", states)
        assert state_b.status == NodeStatus.ERROR
        assert "boom" in state_b.error_message
        mock_store.record_error.assert_called_with("B")
