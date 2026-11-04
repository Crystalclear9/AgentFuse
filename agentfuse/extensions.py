"""Commands for opt-in runtime extensions: python -m agentfuse.extensions."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inference = commands.add_parser("inference-demo", help="compare real cached and uncached attention inference")
    inference.add_argument("--weights", help="JSON weights; defaults to the untrained reference fixture")
    sandbox = commands.add_parser("sandbox", help="run an argv list in an immutable Linux container")
    sandbox.add_argument("--snapshot", required=True)
    sandbox.add_argument("--image", required=True)
    sandbox.add_argument("--timeout", type=float, default=60)
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
    else:
        from .runtime.sandbox import run_isolated
        argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        result = run_isolated(args.snapshot, argv, image=args.image, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("correct", result.get("exit_code", 1) == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
