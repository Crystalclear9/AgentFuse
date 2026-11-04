"""Bounded, observational next-tool predictions. Never establishes causality."""
from collections import Counter, OrderedDict
from threading import RLock


class PathPredictor:
    def __init__(self, max_states: int = 1024):
        if max_states < 1:
            raise ValueError("max_states must be positive")
        self.max_states = max_states
        self.transitions = OrderedDict()
        self.previous = OrderedDict()
        self.invocations = OrderedDict()
        self.lock = RLock()

    def observe(self, stream: str, tool: str) -> None:
        with self.lock:
            previous = self.previous.pop(stream, None)
            if previous is not None:
                counts = self.transitions.pop(previous, Counter())
                counts[tool] += 1
                # Bound both the state count and the outgoing vocabulary.
                if len(counts) > self.max_states:
                    del counts[min(counts, key=counts.get)]
                self.transitions[previous] = counts
                while len(self.transitions) > self.max_states:
                    self.transitions.popitem(last=False)
            self.previous[stream] = tool
            while len(self.previous) > self.max_states:
                self.previous.popitem(last=False)

    def finish(self, stream: str) -> None:
        with self.lock:
            self.previous.pop(stream, None)

    def observe_invocation(self, workflow, invocation, label, parent=None):
        """Learn only an explicitly reported parent/child activation boundary."""
        with self.lock:
            source = self.invocations.get((workflow, parent)) if parent else None
            if source is not None:
                # Isolated stream makes each explicit edge one observation.
                stream = f"edge:{workflow}:{invocation}"
                self.observe(stream, source)
                self.observe(stream, label)
                self.finish(stream)
            self.invocations[(workflow, invocation)] = label
            while len(self.invocations) > self.max_states:
                self.invocations.popitem(last=False)

    def finish_workflow(self, workflow):
        with self.lock:
            for key in list(self.invocations):
                if key[0] == workflow:
                    del self.invocations[key]

    def forecast(self, label, depth=3, limit=5):
        if not 1 <= depth <= 8 or not 1 <= limit <= 20:
            raise ValueError("forecast depth 1..8, limit 1..20")
        paths = [dict(path=[label], probability=1.0)]
        with self.lock:
            for _ in range(depth):
                expanded = []
                for item in paths:
                    choices = self.predict(item["path"][-1], limit)
                    if not choices:
                        expanded.append(item)
                    for choice in choices:
                        expanded.append(dict(path=item["path"] + [choice["tool"]],
                                             probability=item["probability"] * choice["probability"]))
                paths = sorted(expanded, key=lambda item: (-item["probability"], item["path"]))[:limit]
        return paths

    def predict(self, tool: str, limit: int = 5) -> list[dict]:
        with self.lock:
            counts = self.transitions.get(tool, {})
            total = sum(counts.values())
            return [dict(tool=name, probability=count / total, observations=count)
                    for name, count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:max(0, limit)]]
