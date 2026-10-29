# Sample repository

The canonical sample repository lives inside the package so that it is always
available, also after an installation:

```
agentfuse/resources/sample_repository/
```

It contains a small but *real* Python project:

```
src/
    __init__.py
    alpha.py          # pure numeric helpers
    beta.py           # text helpers, imports alpha
    gamma.py          # pipeline module, imports beta and pkg.helper
    zeta_variant.py   # mutated by the divergent demo scenario
    pkg/helper.py     # utility package
docs/architecture.md
```

The offline demo scenarios and the benchmark copy this repository into an
immutable AgentFuse snapshot before any tool is executed.
