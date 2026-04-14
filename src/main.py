"""
FastAPI app for Cloud Run deployment.
Serves the dashboard UI and exposes agent API endpoints.
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from agent import run_agent
import bq_tools
import uuid
import os
from google.cloud import bigquery
import config

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
# Dashboard UI (served at root)
# ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Serve the main dashboard UI."""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r") as f:
        return f.read()


# ──────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────

@app.get("/escalations/count")
def escalations_count():
    return {"count": bq_tools.bq_count_escalations()}

@app.get("/health")
def health_check():
    """Health check for Cloud Run."""
    return {"status": "healthy"}


@app.post("/evaluate")
def evaluate_product_family(request: EvaluateRequest):
    try:
        explanation = run_agent(
            f"Evaluate product family {request.product_family} for repair vs replace. "
            f"Use run_id 'run-{request.product_family}-{uuid.uuid4().hex[:8]}'."
        )

        # Look up the band the agent just wrote (if any)
        rows = list(bq_tools.client.query(
            f"SELECT band FROM `{config.TABLE_SCORES}` WHERE product_family = @pf LIMIT 1",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("pf", "STRING", request.product_family)
            ])
        ).result())
        band = rows[0]["band"] if rows else "ESCALATED"

        return {
            "product_family": request.product_family,
            "band": band,
            "explanation": explanation,
        }
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
def list_families(limit: int = 200):
    """List available product families with volume and data coverage."""
    families = bq_tools.bq_list_product_families(limit=limit)
    return {"families": families}


@app.get("/scores")
def get_scores():
    """Fetch all existing opportunity scores from BigQuery."""
    scores = bq_tools.bq_get_all_scores()
    return {"scores": scores}
