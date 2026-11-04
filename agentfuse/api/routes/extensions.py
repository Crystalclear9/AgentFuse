"""Optional P2 capabilities, visible through the same local management API."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from ...runtime.inference import AttentionModel, InferenceRuntime, reference_weights

router = APIRouter(prefix="/api/extensions", tags=["extensions"])


class InferenceRequest(BaseModel):
    tokens: list[int] = Field(min_length=1, max_length=1024)
    max_new_tokens: int = Field(default=8, ge=0, le=128)
    use_cache: bool = True


@router.get("/predictions")
def predictions(request: Request, tool: str):
    return dict(tool=tool, predictions=request.app.state.runtime.predictor.predict(tool),
                advisory=True)


@router.get("/capabilities")
def capabilities(request: Request):
    runtime = request.app.state.runtime
    return dict(remote_workers=len(runtime.coordinator.remote_endpoints),
                inference_backend="reference-causal-attention", pretrained=False,
                sandbox="docker-linux-opt-in", arbitrary_dependency_completeness=False)


@router.get("/agent-paths")
def agent_paths(request: Request, label: str, depth: int = 3):
    try:
        return dict(paths=request.app.state.runtime.agent_predictor.forecast(label, depth),
                    advisory=True, identity_inference=False)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/inference")
def inference(request: Request, payload: InferenceRequest):
    # A per-application cache, never a module global shared by users or homes.
    runtime = request.app.state.runtime
    try:
        return runtime.inference.generate(runtime.inference_model, payload.tokens,
                                          payload.max_new_tokens, use_cache=payload.use_cache)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/inference/cache")
def clear_cache(request: Request):
    request.app.state.runtime.inference.clear()
    return {"cleared": True}
