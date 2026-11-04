"""Offline causal attention inference with bounded, model-scoped prefix KV reuse.

The reference backend accepts explicit JSON weights. It is a small numerical
inference engine, not a pretrained assistant. No model download or API is used.
"""
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import math
from threading import RLock


def multiply(vector, matrix):
    return tuple(sum(x * row[column] for x, row in zip(vector, matrix))
                 for column in range(len(matrix[0])))


@dataclass(frozen=True)
class Prefix:
    keys: tuple
    values: tuple
    logits: tuple


class AttentionModel:
    def __init__(self, weights: dict):
        # Own the values: caller mutations must never corrupt cache identity.
        weights = json.loads(json.dumps(weights, allow_nan=False))
        embeddings = weights["embeddings"]
        self.vocab, self.width = len(embeddings), len(embeddings[0])
        if not 2 <= self.vocab <= 256 or not 1 <= self.width <= 64:
            raise ValueError("reference model bounds: vocabulary 2..256, width 1..64")
        for name, rows, columns in (("embeddings", self.vocab, self.width),
                                    ("query", self.width, self.width),
                                    ("key", self.width, self.width),
                                    ("value", self.width, self.width),
                                    ("output", self.width, self.vocab)):
            matrix = weights[name]
            if len(matrix) != rows or any(len(row) != columns for row in matrix):
                raise ValueError(f"invalid weight shape: {name}")
            if any(not isinstance(x, (int, float)) or not math.isfinite(x) for row in matrix for x in row):
                raise ValueError("weights must be finite numbers")
        self.model_id = hashlib.sha256(json.dumps(weights, sort_keys=True).encode()).hexdigest()
        from types import MappingProxyType
        self.weights = MappingProxyType({name: tuple(tuple(row) for row in matrix)
                                        for name, matrix in weights.items()})

    def step(self, token: int, state: Prefix) -> Prefix:
        vector = self.weights["embeddings"][token]
        # Positional signal ensures position is part of prefix semantics.
        vector = tuple(x + math.sin((len(state.keys) + 1) / (10000 ** (i / self.width)))
                       for i, x in enumerate(vector))
        query = multiply(vector, self.weights["query"])
        keys = state.keys + (multiply(vector, self.weights["key"]),)
        values = state.values + (multiply(vector, self.weights["value"]),)
        scores = [sum(q * k for q, k in zip(query, key)) / math.sqrt(self.width) for key in keys]
        maximum = max(scores)
        probabilities = [math.exp(x - maximum) for x in scores]
        total = sum(probabilities)
        context = tuple(sum(p * value[i] for p, value in zip(probabilities, values)) / total
                        for i in range(self.width))
        return Prefix(keys, values, multiply(context, self.weights["output"]))


class InferenceRuntime:
    def __init__(self, max_cache_bytes=8 * 1024 * 1024):
        if max_cache_bytes < 0:
            raise ValueError("cache budget cannot be negative")
        self.budget = max_cache_bytes
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.lock = RLock()

    def _put(self, key, state):
        # Account for actual Python object graph size, including shared tuples
        # conservatively per entry. Overcounting is safe for a bounded cache.
        import sys
        def size(value):
            return sys.getsizeof(value) + (sum(size(x) for x in value) if isinstance(value, tuple) else 0)
        cost = size(key) + size((state.keys, state.values, state.logits))
        if cost > self.budget:
            return
        old = self.cache.pop(key, None)
        if old:
            self.cache_bytes -= old[1]
        while self.cache and self.cache_bytes + cost > self.budget:
            _, (_, removed) = self.cache.popitem(last=False)
            self.cache_bytes -= removed
        self.cache[key] = (state, cost)
        self.cache_bytes += cost

    def generate(self, model: AttentionModel, tokens: list[int], max_new_tokens=8, *, use_cache=True):
        tokens = list(tokens)
        if not tokens or len(tokens) + max_new_tokens > 1024 or not 0 <= max_new_tokens <= 128:
            raise ValueError("nonempty prompt, total <=1024 tokens, generation 0..128")
        if any(type(t) is not int or not 0 <= t < model.vocab for t in tokens):
            raise ValueError("token outside model vocabulary")
        with self.lock:
            state, reused = Prefix((), (), ()), 0
            if use_cache:
                for length in range(len(tokens), 0, -1):
                    key = (model.model_id, tuple(tokens[:length]))
                    entry = self.cache.get(key)
                    if entry:
                        self.cache.move_to_end(key)
                        state, reused = entry[0], length
                        break
            evaluated = 0
            for index in range(reused, len(tokens)):
                state = model.step(tokens[index], state)
                evaluated += 1
                if use_cache:
                    self._put((model.model_id, tuple(tokens[:index + 1])), state)
            generated = []
            for _ in range(max_new_tokens):
                token = max(range(model.vocab), key=lambda index: state.logits[index])
                generated.append(token)
                tokens.append(token)
                state = model.step(token, state)
                evaluated += 1
                if use_cache:
                    self._put((model.model_id, tuple(tokens)), state)
            return dict(tokens=generated, logits=list(state.logits), reused_tokens=reused,
                        evaluated_tokens=evaluated, cache_bytes=self.cache_bytes,
                        model_id=model.model_id)

    def clear(self):
        with self.lock:
            self.cache.clear()
            self.cache_bytes = 0


def reference_weights():
    """Small deterministic, untrained fixture for correctness and reuse demos."""
    width, vocab = 8, 16
    def matrix(rows, columns, phase):
        return [[math.sin(i * columns + j + phase) / width for j in range(columns)] for i in range(rows)]
    return dict(embeddings=matrix(vocab, width, 1), query=matrix(width, width, 2),
                key=matrix(width, width, 3), value=matrix(width, width, 4),
                output=matrix(width, vocab, 5))
