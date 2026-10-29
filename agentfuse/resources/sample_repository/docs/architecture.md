# Sample repository architecture

The sample repository is a plain Python project without any runtime
dependency.  It exists so that the AgentFuse demo and benchmark scenarios can
run completely offline.

* `src/alpha.py` - pure numeric helpers.
* `src/beta.py` - text helpers, imports `src/alpha.py`.
* `src/gamma.py` - pipeline module, imports `src/beta.py` and `src/pkg/helper.py`.
* `src/zeta_variant.py` - the module that the divergent demo mutates in one
  branch only.  Its name sorts last so that the conflicting dependency is
  observed *after* the identical dependencies.
* `src/pkg/helper.py` - utility package.
