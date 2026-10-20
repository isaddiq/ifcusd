"""IFCX layer federation and composition.

Ported from buildingSMART IFC5-development ``src/ifcx-core/composition``:

1. *Federation*: all nodes of all layers are collected in layer order.
2. *Flattening*: nodes with the same path are merged; later layers win, and a
   ``null`` child/inherit/attribute deletes the entry.
3. *Composition*: ``inherits`` copy the children and attributes of the
   referenced node; ``children`` instantiate the referenced node under a name.

Differences from the reference implementation: composed class nodes are
memoised and copied on use (the TypeScript version shares and mutates them),
and composition cycles raise :class:`IfcxCompositionError` with the path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ifcusd.errors import InputError


class IfcxCompositionError(InputError):
    """The IFCX data cannot be composed (cycle or unknown reference)."""


@dataclass
class IfcxLayer:
    path: Path
    sha256: str
    header: dict[str, Any]
    imports: list[dict[str, Any]]
    schemas: dict[str, Any]
    data: list[dict[str, Any]]


@dataclass
class PreNode:
    path: str
    children: dict[str, str | None] = field(default_factory=dict)
    inherits: dict[str, str] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class ComposedNode:
    source: str  # IFCX path this node was composed from
    attributes: dict[str, Any] = field(default_factory=dict)
    children: dict[str, ComposedNode] = field(default_factory=dict)

    def copy(self) -> ComposedNode:
        return ComposedNode(
            self.source,
            dict(self.attributes),
            {name: child.copy() for name, child in self.children.items()},
        )


def load_layer(path: str | Path) -> IfcxLayer:
    source = Path(path)
    raw = source.read_bytes()
    try:
        document = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"{source} is not a valid IFCX (JSON) file: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("data"), list):
        raise InputError(f"{source} is not an IFCX file (missing 'data' array)")
    return IfcxLayer(
        path=source,
        sha256=hashlib.sha256(raw).hexdigest(),
        header=dict(document.get("header") or {}),
        imports=list(document.get("imports") or []),
        schemas=dict(document.get("schemas") or {}),
        data=document["data"],
    )


def flatten(layers: list[IfcxLayer]) -> dict[str, PreNode]:
    nodes: dict[str, PreNode] = {}
    for layer in layers:
        for raw in layer.data:
            path = raw.get("path")
            if not isinstance(path, str):
                continue
            node = nodes.setdefault(path, PreNode(path))
            for name, child in (raw.get("children") or {}).items():
                node.children[name] = child
            for name, inherit in (raw.get("inherits") or {}).items():
                if inherit is None:
                    node.inherits.pop(name, None)
                else:
                    node.inherits[name] = inherit
            for name, value in (raw.get("attributes") or {}).items():
                node.attributes[name] = value
    return nodes


def find_roots(nodes: dict[str, PreNode]) -> list[str]:
    """Paths no other node refers to (the composition roots); raises on cycles."""
    dependents: set[str] = set()
    dependencies: dict[str, list[str]] = {}
    for path, node in nodes.items():
        targets = [t for t in node.inherits.values() if t] + [t for t in node.children.values() if t]
        dependents.update(targets)
        dependencies.setdefault(_head(path), []).extend(_head(t) for t in targets)

    visiting, done = 1, 2
    state: dict[str, int] = {}
    for start in sorted(dependencies):
        if state.get(start) == done:
            continue
        state[start] = visiting
        stack = [(start, iter(dependencies.get(start, ())))]
        while stack:
            current, pending = stack[-1]
            following = next(pending, None)
            if following is None:
                state[current] = done
                stack.pop()
            elif state.get(following) == visiting:
                raise IfcxCompositionError(f"composition cycle through {following}")
            elif state.get(following) is None:
                state[following] = visiting
                stack.append((following, iter(dependencies.get(following, ()))))
    return [path for path in sorted(nodes) if path not in dependents and "/" not in path]


class Composer:
    def __init__(self, nodes: dict[str, PreNode]) -> None:
        self.nodes = nodes
        self._memo: dict[str, ComposedNode] = {}
        self._active: set[str] = set()

    def compose(self, path: str) -> ComposedNode:
        cached = self._memo.get(path)
        if cached is not None:
            return cached
        if path in self._active:
            raise IfcxCompositionError(f"composition cycle through {path}")
        self._active.add(path)
        try:
            node = ComposedNode(path)
            self._compose_into(path, node)
        finally:
            self._active.discard(path)
        self._memo[path] = node
        return node

    def _compose_into(self, path: str, node: ComposedNode) -> None:
        pre = self.nodes.get(path)
        if pre is not None:
            for inherit in pre.inherits.values():
                source = self._resolve(inherit)
                for name, child in source.children.items():
                    node.children[name] = child.copy()
                node.attributes.update(source.attributes)
            for name, child_path in pre.children.items():
                if child_path is None:
                    node.children.pop(name, None)
                else:
                    node.children[name] = self._resolve(child_path).copy()
            node.attributes.update(pre.attributes)
        for name, child in list(node.children.items()):
            nested = f"{path}/{name}"
            if nested in self.nodes:  # overrides authored on a nested path
                self._compose_into(nested, child)

    def _resolve(self, reference: str) -> ComposedNode:
        head, tail = _head(reference), _tail(reference)
        node = self.compose(head)
        for part in [p for p in tail.split("/") if p]:
            child = node.children.get(part)
            if child is None:
                raise IfcxCompositionError(f"unknown IFCX node {reference}")
            node = child
        return node


def _head(path: str) -> str:
    return path.split("/")[0]


def _tail(path: str) -> str:
    parts = path.split("/")
    return "/".join(parts[1:])
