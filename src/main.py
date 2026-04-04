"""
FastAPI app for Cloud Run deployment.
Exposes the agent as an API endpoint.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from agent import run_agent
import bq_tools
import uuid

app = FastAPI(
    title="RMA Repair-vs-Replace Agent",
    description="Agentic AI for Cisco RMA cost optimization",
    version="0.1.0",
)


# ──────────────────────────────────────────────
# Request/Response models
# ──────────────────────────────────────────────

class EvaluateRequest(BaseModel):
    product_family: str

class BatchEvaluateRequest(BaseModel):
    product_families: list[str]

class AgentResponse(BaseModel):
    product_family: str
    explanation: str


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────

@app.get("/health")
def health_check():
    """Health check for Cloud Run."""
    return {"status": "healthy"}


@app.post("/evaluate", response_model=AgentResponse)
def evaluate_product_family(request: EvaluateRequest):
    """
    Evaluate a single product family for repair vs replace.
    The agent fetches data, scores it, and writes a recommendation.
    """
    try:
        explanation = run_agent(
            f"Evaluate product family {request.product_family} for repair vs replace. "
            f"Use run_id 'run-{request.product_family}-{uuid.uuid4().hex[:8]}'."
        )
        return AgentResponse(
            product_family=request.product_family,
            explanation=explanation,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/evaluate-batch")
def evaluate_batch(request: BatchEvaluateRequest):
    """Evaluate multiple product families."""
    results = []
    for pf in request.product_families:
        try:
            explanation = run_agent(
                f"Evaluate product family {pf} for repair vs replace. "
                f"Use run_id 'run-{pf}-{uuid.uuid4().hex[:8]}'."
            )
            results.append({
                "product_family": pf,
                "explanation": explanation,
                "status": "success",
            })
        except Exception as e:
            results.append({
                "product_family": pf,
                "explanation": str(e),
                "status": "error",
            })
    return {"results": results}


@app.get("/families")
def list_families(limit: int = 50):
    """List available product families with volume and data coverage."""
    families = bq_tools.bq_list_product_families(limit=limit)
    return {"families": families}
