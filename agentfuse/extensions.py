"""Commands for opt-in runtime extensions: python -m agentfuse.extensions."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inference = commands.add_parser("inference-demo", help="compare real cached and uncached attention inference")
    inference.add_argument("--weights", help="JSON weights; defaults to the untrained reference fixture")
    language = commands.add_parser('llm-demo', help='compare pretrained local model KV reuse')
    language.add_argument('--config', default='config/p2-local.toml')
    language.add_argument('--prompt', default='The capital of France is')
    sandbox = commands.add_parser("sandbox", help="run an argv list in an immutable Linux container")
    sandbox.add_argument("--snapshot", required=True)
    sandbox.add_argument("--image", required=True)
    sandbox.add_argument("--timeout", type=float, default=60)
    sandbox.add_argument('--trace', action='store_true', help='audit native Linux descendant syscalls')
    sandbox.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == "inference-demo":
        import time
        from .runtime.inference import AttentionModel, InferenceRuntime, reference_weights
        weights = json.loads(Path(args.weights).read_text(encoding="utf-8")) if args.weights else reference_weights()
        model, runtime = AttentionModel(weights), InferenceRuntime()
        prompt = [index % model.vocab for index in range(64)]
        started = time.perf_counter()
        baseline = runtime.generate(model, prompt, 8, use_cache=False)
        baseline_seconds = time.perf_counter() - started
        runtime.generate(model, prompt, 0)
        started = time.perf_counter()
        shared = runtime.generate(model, prompt, 8)
        shared_seconds = time.perf_counter() - started
        result = dict(backend="causal-attention", pretrained=bool(args.weights),
                      fixture=not bool(args.weights), baseline=baseline, cached=shared,
                      baseline_seconds=baseline_seconds, cached_seconds=shared_seconds,
                      correct=baseline["tokens"] == shared["tokens"] and baseline["logits"] == shared["logits"])
        # Supplying weights does not imply they were trained.
        result["pretrained"] = "unknown" if args.weights else False
    elif args.command == 'llm-demo':
        from .app import AgentFuseApp
        from .settings import load_settings
        app = AgentFuseApp(settings=load_settings(args.config))
        try:
            if app.llm is None:
                raise ValueError('configure inference.backend = llama.cpp')
            cold = app.llm.generate(args.prompt, 16, use_cache=False)
            warm = app.llm.generate(args.prompt, 16)
            result = dict(backend='llama.cpp', baseline=cold, cached=warm,
                          correct=cold['tokens'] == warm['tokens'],
                          reduced_prompt_work=warm['evaluated_tokens'] < cold['evaluated_tokens'])
        finally:
            app.close_sync()
    else:
        from .runtime.sandbox import run_isolated
        from .runtime.native_trace import run_traced
        argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        runner = run_traced if args.trace else run_isolated
        result = runner(args.snapshot, argv, image=args.image, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("correct", result.get("exit_code", 1) == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
