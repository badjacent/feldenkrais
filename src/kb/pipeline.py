"""Pipeline dependency graph for the book generation system.

Models the data pipeline as a DAG where every node is a function:
  dict[str, text] → text

Cache key = hash of all inputs. If any input hash changes, the node is stale
and all downstream nodes become stale too.
"""

import argparse
import hashlib
import json
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


class NodeKind(Enum):
    SOURCE = "source"
    TRANSFORM = "transform"


class NodeStatus(Enum):
    FRESH = "fresh"
    STALE = "stale"
    RUNNING = "running"
    ERROR = "error"
    PENDING = "pending"


@dataclass
class NodeSpec:
    """Defines a node in the pipeline graph (static structure)."""

    name: str
    kind: NodeKind
    input_names: list[str]  # upstream node names (empty for sources)
    transform: Optional[Callable] = None  # for transforms: fn(**inputs) → output
    hash_fn: Optional[Callable[[], str]] = None  # for sources: returns content hash
    description: str = ""
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class NodeState:
    """Persisted state for a node (from the last run)."""

    name: str
    status: NodeStatus = NodeStatus.PENDING
    input_hash: str = ""  # hash of inputs at time of last run
    output_hash: str = ""  # hash of the output produced
    last_run_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    error_message: Optional[str] = None


class PipelineGraph:
    """DAG of pipeline nodes with dependency tracking."""

    def __init__(self):
        self.nodes: dict[str, NodeSpec] = {}
        self._topo_cache: Optional[list[str]] = None

    def add_source(
        self,
        name: str,
        hash_fn: Callable[[], str],
        description: str = "",
        tags: Optional[dict[str, str]] = None,
    ) -> "PipelineGraph":
        if name in self.nodes:
            raise ValueError(f"Duplicate node name: {name}")
        self.nodes[name] = NodeSpec(
            name=name,
            kind=NodeKind.SOURCE,
            input_names=[],
            hash_fn=hash_fn,
            description=description,
            tags=tags or {},
        )
        self._topo_cache = None
        return self

    def add_transform(
        self,
        name: str,
        input_names: list[str],
        transform: Callable,
        description: str = "",
        tags: Optional[dict[str, str]] = None,
    ) -> "PipelineGraph":
        if name in self.nodes:
            raise ValueError(f"Duplicate node name: {name}")
        for inp in input_names:
            if inp not in self.nodes:
                raise ValueError(f"Unknown input '{inp}' for node '{name}'")
        self.nodes[name] = NodeSpec(
            name=name,
            kind=NodeKind.TRANSFORM,
            input_names=input_names,
            transform=transform,
            description=description,
            tags=tags or {},
        )
        self._topo_cache = None
        return self

    def topological_order(self) -> list[str]:
        """Return node names in topological order (Kahn's algorithm)."""
        if self._topo_cache is not None:
            return self._topo_cache

        in_degree: dict[str, int] = {n: 0 for n in self.nodes}
        children: dict[str, list[str]] = {n: [] for n in self.nodes}
        for name, spec in self.nodes.items():
            for inp in spec.input_names:
                children[inp].append(name)
                in_degree[name] += 1

        queue = deque(n for n, d in in_degree.items() if d == 0)
        result = []
        while queue:
            node = queue.popleft()
            result.append(node)
            for child in children[node]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(result) != len(self.nodes):
            raise ValueError("Cycle detected in pipeline graph")

        self._topo_cache = result
        return result

    def descendants(self, name: str) -> set[str]:
        """Return all downstream nodes (transitive)."""
        children: dict[str, list[str]] = {n: [] for n in self.nodes}
        for n, spec in self.nodes.items():
            for inp in spec.input_names:
                children[inp].append(n)

        visited: set[str] = set()
        queue = deque(children.get(name, []))
        while queue:
            node = queue.popleft()
            if node not in visited:
                visited.add(node)
                queue.extend(children.get(node, []))
        return visited

    def ancestors(self, name: str) -> set[str]:
        """Return all upstream nodes (transitive)."""
        visited: set[str] = set()
        queue = deque(self.nodes[name].input_names)
        while queue:
            node = queue.popleft()
            if node not in visited:
                visited.add(node)
                queue.extend(self.nodes[node].input_names)
        return visited


class PipelineStateStore:
    """Persists node state to the pipeline_node_state table."""

    def __init__(self, db):
        self.db = db

    def initialize(self):
        """Create the pipeline_node_state table if it doesn't exist."""
        self.db.ensure_pipeline_node_state_table()

    def load_all(self) -> dict[str, NodeState]:
        """Load all persisted node states."""
        with self.db.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT * FROM pipeline_node_state")
            result = {}
            for row in cursor.fetchall():
                result[row["name"]] = NodeState(
                    name=row["name"],
                    status=NodeStatus(row["status"]),
                    input_hash=row["input_hash"],
                    output_hash=row["output_hash"],
                    last_run_at=str(row["last_run_at"]) if row["last_run_at"] else None,
                    duration_seconds=row["duration_seconds"],
                    error_message=row["error_message"],
                )
            return result

    def save(self, state: NodeState):
        """Upsert a single node state."""
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pipeline_node_state
                    (name, status, input_hash, output_hash, last_run_at,
                     duration_seconds, error_message, updated_at)
                VALUES (%s, %s, %s, %s, NOW(), %s, %s, NOW())
                ON CONFLICT (name) DO UPDATE SET
                    status = EXCLUDED.status,
                    input_hash = EXCLUDED.input_hash,
                    output_hash = EXCLUDED.output_hash,
                    last_run_at = EXCLUDED.last_run_at,
                    duration_seconds = EXCLUDED.duration_seconds,
                    error_message = EXCLUDED.error_message,
                    updated_at = NOW()
                """,
                (
                    state.name,
                    state.status.value,
                    state.input_hash,
                    state.output_hash,
                    state.duration_seconds,
                    state.error_message,
                ),
            )

    def invalidate(self, name: str):
        """Mark a node as stale by clearing its input hash."""
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE pipeline_node_state
                SET input_hash = '', status = 'stale', updated_at = NOW()
                WHERE name = %s
                """,
                (name,),
            )

    def load_enabled_flags(self) -> dict[str, bool]:
        """Load enabled flags for all nodes. Default is True (enabled)."""
        with self.db.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT name, metadata FROM pipeline_node_state WHERE metadata IS NOT NULL"
            )
            flags = {}
            for row in cursor.fetchall():
                meta = row["metadata"]
                if isinstance(meta, dict) and "enabled" in meta:
                    flags[row["name"]] = bool(meta["enabled"])
            return flags

    def is_enabled(self, name: str) -> bool:
        """Check if a node is enabled (default: True)."""
        with self.db.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT metadata FROM pipeline_node_state WHERE name = %s",
                (name,),
            )
            row = cursor.fetchone()
            if row and row["metadata"] and isinstance(row["metadata"], dict):
                return row["metadata"].get("enabled", True)
            return True

    def set_enabled(self, name: str, enabled: bool):
        """Set the enabled flag for a node in the metadata JSONB column."""
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pipeline_node_state (name, status, input_hash, output_hash, metadata)
                VALUES (%s, 'pending', '', '', %s::jsonb)
                ON CONFLICT (name) DO UPDATE SET
                    metadata = COALESCE(pipeline_node_state.metadata, '{}'::jsonb)
                              || %s::jsonb,
                    updated_at = NOW()
                """,
                (
                    name,
                    json.dumps({"enabled": enabled}),
                    json.dumps({"enabled": enabled}),
                ),
            )

    def clear(self, prefix: Optional[str] = None):
        """Delete state entries. If prefix given, only matching names."""
        with self.db.get_cursor() as cursor:
            if prefix:
                cursor.execute(
                    "DELETE FROM pipeline_node_state WHERE name LIKE %s",
                    (prefix + "%",),
                )
            else:
                cursor.execute("DELETE FROM pipeline_node_state")


def _compute_input_hash_for_source(spec: NodeSpec) -> str:
    """Compute the current content hash for a source node."""
    assert spec.kind.value == "source" and spec.hash_fn is not None
    return spec.hash_fn()


def _compute_input_hash_for_transform(
    spec: NodeSpec, states: dict[str, NodeState]
) -> str:
    """Compute input hash for a transform = hash of upstream output hashes."""
    parts = []
    for inp_name in sorted(spec.input_names):
        upstream_state = states.get(inp_name)
        output_hash = upstream_state.output_hash if upstream_state else ""
        parts.append(f"{inp_name}={output_hash}")
    combined = "|".join(parts)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


class PipelineExecutor:
    """Runs pipeline nodes in topological order, tracking staleness."""

    def __init__(self, graph: PipelineGraph, store: PipelineStateStore):
        self.graph = graph
        self.store = store

    def compute_staleness(self) -> dict[str, NodeState]:
        """Check every node and return current states with staleness flags."""
        stored = self.store.load_all()
        states: dict[str, NodeState] = {}

        for name in self.graph.topological_order():
            spec = self.graph.nodes[name]
            old = stored.get(name)

            if spec.kind.value == "source":
                current_hash = _compute_input_hash_for_source(spec)
            else:
                current_hash = _compute_input_hash_for_transform(spec, states)

            if old is None:
                # Never run
                state = NodeState(
                    name=name,
                    status=NodeStatus.PENDING,
                    input_hash=current_hash,
                )
            elif old.status == NodeStatus.RUNNING:
                # Check if it's a zombie (stuck RUNNING for >10 min)
                is_zombie = False
                if old.last_run_at:
                    from datetime import datetime, timezone, timedelta
                    try:
                        started = datetime.fromisoformat(
                            old.last_run_at.replace("Z", "+00:00")
                        )
                        if started.tzinfo is None:
                            started = started.replace(tzinfo=timezone.utc)
                        age = datetime.now(timezone.utc) - started
                        is_zombie = age > timedelta(minutes=5)
                    except Exception:
                        is_zombie = True

                if is_zombie:
                    # Treat as stale so it gets retried
                    state = NodeState(
                        name=name,
                        status=NodeStatus.STALE,
                        input_hash=current_hash,
                        output_hash=old.output_hash,
                        last_run_at=old.last_run_at,
                        duration_seconds=old.duration_seconds,
                    )
                else:
                    # Currently executing — preserve RUNNING status
                    state = NodeState(
                        name=name,
                        status=NodeStatus.RUNNING,
                        input_hash=current_hash,
                        output_hash=old.output_hash,
                        last_run_at=old.last_run_at,
                        duration_seconds=old.duration_seconds,
                    )
            elif old.status == NodeStatus.ERROR:
                # Keep error status visible, but update hash
                state = NodeState(
                    name=name,
                    status=NodeStatus.ERROR,
                    input_hash=current_hash,
                    output_hash=old.output_hash,
                    last_run_at=old.last_run_at,
                    duration_seconds=old.duration_seconds,
                    error_message=old.error_message,
                )
            elif current_hash != old.input_hash:
                # Inputs changed → stale
                state = NodeState(
                    name=name,
                    status=NodeStatus.STALE,
                    input_hash=current_hash,
                    output_hash=old.output_hash,
                    last_run_at=old.last_run_at,
                    duration_seconds=old.duration_seconds,
                )
            elif old.status == NodeStatus.STALE or not old.output_hash:
                # Was manually invalidated or never completed → stay stale
                state = NodeState(
                    name=name,
                    status=NodeStatus.STALE,
                    input_hash=current_hash,
                    output_hash=old.output_hash,
                    last_run_at=old.last_run_at,
                    duration_seconds=old.duration_seconds,
                )
            else:
                # Hash matches and has output → fresh
                state = NodeState(
                    name=name,
                    status=NodeStatus.FRESH,
                    input_hash=old.input_hash,
                    output_hash=old.output_hash,
                    last_run_at=old.last_run_at,
                    duration_seconds=old.duration_seconds,
                )

            # Cascade: if any upstream is not fresh, this node is stale too
            if spec.kind.value == "transform" and state.status == NodeStatus.FRESH:
                for inp_name in spec.input_names:
                    upstream = states.get(inp_name)
                    if upstream and upstream.status not in (
                        NodeStatus.FRESH,
                    ):
                        state.status = NodeStatus.STALE
                        break

            states[name] = state

        return states

    def run_node(self, name: str, states: dict[str, NodeState]) -> NodeState:
        """Execute a single node and return its updated state."""
        spec = self.graph.nodes[name]

        if spec.kind.value == "source":
            # Sources don't have a transform — just hash and mark fresh
            content_hash = _compute_input_hash_for_source(spec)
            state = NodeState(
                name=name,
                status=NodeStatus.FRESH,
                input_hash=content_hash,
                output_hash=content_hash,  # source output = its content hash
            )
            self.store.save(state)
            states[name] = state
            return state

        # Transform node — gather inputs and run
        input_hash = _compute_input_hash_for_transform(spec, states)
        state = NodeState(name=name, status=NodeStatus.RUNNING, input_hash=input_hash)
        self.store.save(state)  # persist RUNNING so dashboard shows live progress

        t0 = time.time()
        try:
            # Build input dict: {upstream_name: upstream_output_hash}
            # The transform function receives the input names so it can
            # look up actual data from cache or DB
            inputs = {inp: states[inp].output_hash for inp in spec.input_names}
            result = spec.transform(**inputs)
            elapsed = time.time() - t0

            output_hash = hashlib.sha256(
                result.encode() if isinstance(result, str) else json.dumps(result).encode()
            ).hexdigest()[:16]

            state = NodeState(
                name=name,
                status=NodeStatus.FRESH,
                input_hash=input_hash,
                output_hash=output_hash,
                duration_seconds=round(elapsed, 1),
            )
        except Exception as e:
            elapsed = time.time() - t0
            state = NodeState(
                name=name,
                status=NodeStatus.ERROR,
                input_hash=input_hash,
                duration_seconds=round(elapsed, 1),
                error_message=str(e)[:500],
            )

        self.store.save(state)
        states[name] = state
        return state

    def run_stale(
        self,
        dry_run: bool = False,
        target_node: Optional[str] = None,
    ) -> list[NodeState]:
        """Run all stale/pending nodes in topological order.

        If target_node is given, only run that node and its stale ancestors.
        Returns list of states for nodes that were (or would be) executed.
        """
        states = self.compute_staleness()
        order = self.graph.topological_order()

        # Determine which nodes need running
        needs_run = set()
        for name in order:
            st = states[name]
            if st.status in (NodeStatus.STALE, NodeStatus.PENDING, NodeStatus.ERROR):
                needs_run.add(name)

        # If targeting a specific node, filter to it + its stale ancestors
        if target_node:
            if target_node not in self.graph.nodes:
                raise ValueError(f"Unknown node: {target_node}")
            ancestors = self.graph.ancestors(target_node) | {target_node}
            needs_run = needs_run & ancestors

        # Skip disabled nodes
        enabled_flags = self.store.load_enabled_flags()
        disabled = {name for name in needs_run if not enabled_flags.get(name, True)}
        if disabled:
            for name in sorted(disabled):
                print(f"  [skip] {name} (disabled)")
            needs_run -= disabled

        to_run = [n for n in order if n in needs_run]

        if dry_run:
            return [states[n] for n in to_run]

        results = []
        total = len(to_run)
        for i, name in enumerate(to_run, 1):
            spec = self.graph.nodes[name]

            # Skip if any ancestor is not fresh (errored, running, or skipped)
            blocked = False
            if spec.kind.value == "transform":
                for inp in spec.input_names:
                    upstream = states.get(inp)
                    if upstream and upstream.status not in (NodeStatus.FRESH,):
                        blocked = True
                        break

            if blocked:
                print(f"[{i}/{total}] Skipping {name} (upstream not ready)")
                state = NodeState(
                    name=name,
                    status=NodeStatus.STALE,
                    input_hash=states[name].input_hash if name in states else "",
                )
                states[name] = state
                results.append(state)
                continue

            print(f"[{i}/{total}] Running {name}...", end=" ", flush=True)
            state = self.run_node(name, states)
            if state.status == NodeStatus.FRESH:
                dur = f"({state.duration_seconds}s)" if state.duration_seconds else ""
                print(f"done {dur}")
            else:
                print(f"ERROR: {state.error_message}")
            results.append(state)

        return results


# ── CLI ──────────────────────────────────────────────────────────────────


def _format_status(status: NodeStatus) -> str:
    """Format status with uppercase for attention-worthy states."""
    if status in (NodeStatus.STALE, NodeStatus.ERROR, NodeStatus.PENDING):
        return status.value.upper()
    return status.value


def _print_status(graph: PipelineGraph, states: dict[str, NodeState], stale_only: bool):
    """Print the status table."""
    order = graph.topological_order()
    stale_count = sum(
        1
        for s in states.values()
        if s.status in (NodeStatus.STALE, NodeStatus.PENDING, NodeStatus.ERROR)
    )
    total = len(states)

    print(f"{'Node':<32} {'Status':<10} {'Last Run':<14} {'Duration':<10}")
    print("─" * 66)

    for name in order:
        state = states.get(name, NodeState(name=name))
        spec = graph.nodes[name]

        if stale_only and state.status == NodeStatus.FRESH:
            continue

        # Indent transform nodes under their source
        indent = "  " if spec.kind.value == "transform" else ""
        display_name = f"{indent}{name}"

        status_str = _format_status(state.status)
        last_run = state.last_run_at[:10] if state.last_run_at else "-"
        duration = f"{state.duration_seconds}s" if state.duration_seconds else "-"

        if state.status == NodeStatus.ERROR:
            print(f"{display_name:<32} {status_str:<10} {last_run:<14} {duration:<10}")
            if state.error_message:
                print(f"    error: {state.error_message[:60]}")
        else:
            print(f"{display_name:<32} {status_str:<10} {last_run:<14} {duration:<10}")

    print()
    print(f"{stale_count}/{total} stale")


def _print_graph(graph: PipelineGraph):
    """Print the dependency graph structure."""
    order = graph.topological_order()
    children: dict[str, list[str]] = {n: [] for n in graph.nodes}
    for name, spec in graph.nodes.items():
        for inp in spec.input_names:
            children[inp].append(name)

    print(f"Pipeline graph: {len(graph.nodes)} nodes\n")

    for name in order:
        spec = graph.nodes[name]
        kind = spec.kind.value
        desc = f" — {spec.description}" if spec.description else ""
        deps = children[name]
        dep_str = f" → [{', '.join(deps)}]" if deps else ""
        tags_str = f" {spec.tags}" if spec.tags else ""
        print(f"  [{kind}] {name}{desc}{tags_str}{dep_str}")


def main():
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="Book pipeline dependency graph manager",
    )
    sub = parser.add_subparsers(dest="command")

    # status
    status_p = sub.add_parser("status", help="Show node staleness")
    status_p.add_argument("--stale", action="store_true", help="Only show stale nodes")

    # run
    run_p = sub.add_parser("run", help="Rebuild stale nodes")
    run_p.add_argument("--node", help="Target a specific node (+ stale ancestors)")
    run_p.add_argument("--dry-run", action="store_true", help="Show what would run")

    # graph
    sub.add_parser("graph", help="Print graph structure")

    # invalidate
    inv_p = sub.add_parser("invalidate", help="Force-invalidate nodes by pattern")
    inv_p.add_argument("pattern", help="Name prefix or glob pattern (e.g. 'analysis_b:*')")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Late imports to avoid loading everything for --help
    import os

    from dotenv import load_dotenv

    # Add src/kb to path so web.py's bare imports (from llm import LLM) work
    kb_dir = str(Path(__file__).parent)
    if kb_dir not in sys.path:
        sys.path.insert(0, kb_dir)

    load_dotenv()

    try:
        from database import Database
    except ImportError:
        from src.kb.database import Database

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    db = Database(db_url)

    try:
        from pipeline_graph import build_book_pipeline
    except ImportError:
        from src.kb.pipeline_graph import build_book_pipeline

    graph = build_book_pipeline(db)
    store = PipelineStateStore(db)
    store.initialize()
    executor = PipelineExecutor(graph, store)

    if args.command == "status":
        states = executor.compute_staleness()
        _print_status(graph, states, stale_only=args.stale)

    elif args.command == "run":
        if args.dry_run:
            results = executor.run_stale(dry_run=True, target_node=args.node)
            if results:
                print(f"Would run {len(results)} node(s):")
                for s in results:
                    print(f"  {s.name} ({_format_status(s.status)})")
            else:
                print("Nothing to run — all nodes fresh.")
        else:
            results = executor.run_stale(target_node=args.node)
            fresh = sum(1 for r in results if r.status == NodeStatus.FRESH)
            errors = sum(1 for r in results if r.status == NodeStatus.ERROR)
            if results:
                print(f"\nComplete: {fresh} succeeded, {errors} errors")
            else:
                print("Nothing to run — all nodes fresh.")

    elif args.command == "graph":
        _print_graph(graph)

    elif args.command == "invalidate":
        pattern = args.pattern.replace("*", "%")
        store.clear(prefix=pattern.rstrip("%"))
        print(f"Invalidated nodes matching '{args.pattern}'")

    db.close()


if __name__ == "__main__":
    main()
