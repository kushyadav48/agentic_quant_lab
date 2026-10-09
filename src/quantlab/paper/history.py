"""Private immutable append-only history and fixed-depth retry indexes.

Only consumer snapshots traverse history. Trie updates copy at most 64 nodes
of at most 16 children; they never copy retained entries. No mutable staging
is needed for order preparation or account liquidity publication.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib


@dataclass(frozen=True, slots=True)
class History:
    previous: "History | None" = None
    chunk: tuple = ()
    encoded_size: int = 0
    count: int = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "count", (0 if self.previous is None else self.previous.count) + len(self.chunk))

    def append(self, chunk, wires):
        if not chunk:
            return self
        size = self.encoded_size + sum(len(w.encode("utf-8")) for w in wires)
        size += len(chunk) - (1 if not self.count else 0)
        return History(self, chunk, size)

    def __len__(self):
        return self.count

    def __iter__(self):
        nodes, node = [], self
        while node is not None:
            nodes.append(node)
            node = node.previous
        for node in reversed(nodes):
            yield from node.chunk

    def __reversed__(self):
        node = self
        while node is not None:
            yield from reversed(node.chunk)
            node = node.previous

    def __getitem__(self, index):
        if index == -1 and self.chunk:
            return self.chunk[-1]
        raise IndexError("private history supports only the final item")


@dataclass(frozen=True)
class RetainedMap(Mapping):
    root: dict = field(default_factory=dict)
    count: int = 0

    def __len__(self):
        return self.count

    def __getitem__(self, key):
        path = hashlib.sha256(key.encode("utf-8")).hexdigest()
        node = self.root
        for digit in path:
            node = node[digit]
        stored, value = node
        if stored != key:
            raise KeyError(key)
        return value

    def __iter__(self):
        def keys(node, depth):
            if depth == 64:
                yield node[0]
            else:
                for child in node.values():
                    yield from keys(child, depth + 1)
        yield from keys(self.root, 0)

    def set(self, key, value):
        path = hashlib.sha256(key.encode("utf-8")).hexdigest()
        present = key in self

        def update(node, depth):
            if depth == 64:
                if node and node[0] != key:
                    raise ValueError("retained index hash collision")
                return (key, value)
            updated = dict(node)
            digit = path[depth]
            updated[digit] = update(node.get(digit, {}), depth + 1)
            return updated
        return RetainedMap(update(self.root, 0), self.count + int(not present))
