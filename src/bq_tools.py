"""
BigQuery tool functions for the Repair-vs-Replace agent.
These are the tools Gemini calls via function calling.
All cost math is deterministic — Gemini never computes numbers.
"""

from google.cloud import bigquery
from datetime import datetime
import uuid
import config

client = bigquery.Client(project=config.PROJECT_ID)


# ──────────────────────────────────────────────
# TOOL 1: Get context for a product family
# ──────────────────────────────────────────────

def bq_get_part_family_context(product_family: str) -> dict:
    """
    Fetch volume, cost, and repair performance data for a product family.
    Returns everything the agent needs to decide whether to score or escalate.
    """
    query = f"""
    SELECT
      c.product_family,
      d.annualized_volume,
      d.rma_cases_total,
      c.avg_replace_unit_cost,
      c.median_replace_unit_cost,
      c.avg_repair_cost,
      c.median_repair_cost_when_repaired,
      c.avg_logistics_cost,
      c.avg_labor_cost,
      c.avg_duty_vat_cost,
      c.avg_total_fulfillment_cost,
      c.cases_with_repair_cost,
      c.cases_with_replace_cost,
      c.total_rma_cases
    FROM `{config.TABLE_UNIT_COSTS}` c
    JOIN `{config.TABLE_DEMAND}` d
      ON c.product_family = d.product_family
    WHERE c.product_family = @product_family
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("product_family", "STRING", product_family)
        ]
    )

    results = client.query(query, job_config=job_config).result()
    rows = [dict(row) for row in results]

    if not rows:
        return {"error": f"No data found for product family: {product_family}"}

    return rows[0]


# ──────────────────────────────────────────────
# TOOL 2: Deterministic scoring
# ──────────────────────────────────────────────

def score_net_annual_benefit(
    annualized_volume: int,
    replace_unit_cost: float,
    repair_unit_cost: float,
    risk_buffer_pct: float = config.DEFAULT_RISK_BUFFER_PCT,
    fixed_cost: float = config.DEFAULT_FIXED_COST,
    min_benefit_threshold: float = config.DEFAULT_MIN_BENEFIT
) -> dict:
    """
    Deterministic net annual benefit calculation.
    No LLM involved — pure math.
    """
    per_unit_delta = replace_unit_cost - repair_unit_cost
    gross_annual_savings = per_unit_delta * annualized_volume
    risk_buffer = abs(gross_annual_savings) * risk_buffer_pct
    net_annual_benefit = gross_annual_savings - fixed_cost - risk_buffer

    # Banding
    if net_annual_benefit >= min_benefit_threshold:
        band = "GREEN"
        decision = "REPAIR"
    elif per_unit_delta > 0:
        band = "YELLOW"
        decision = "REVIEW"
    else:
        band = "RED"
        decision = "REPLACE"

    # Top drivers
    drivers = []
    if annualized_volume >= 100:
        drivers.append("High volume")
    if per_unit_delta > 500:
        drivers.append("Large cost delta")
    if per_unit_delta < 0:
        drivers.append("Repair more expensive than replace")
    if annualized_volume < config.DEFAULT_MIN_VOLUME:
        drivers.append("Below volume threshold")

    return {
        "per_unit_delta": round(per_unit_delta, 2),
        "gross_annual_savings": round(gross_annual_savings, 2),
        "risk_buffer": round(risk_buffer, 2),
        "fixed_enablement_cost": round(fixed_cost, 2),
        "net_annual_benefit": round(net_annual_benefit, 2),
        "band": band,
        "decision": decision,
        "top_drivers": drivers,
    }


# ──────────────────────────────────────────────
# TOOL 3: Write recommendation to BigQuery
# ──────────────────────────────────────────────

def bq_write_recommendation(
    run_id: str,
    product_family: str,
    decision: str,
    band: str,
    net_benefit_usd: float,
    top_drivers: list,
    explanation: str
) -> dict:
    """Write a recommendation row to the BigQuery output table."""
    row = {
        "run_id": run_id,
        "product_family": product_family,
        "decision": decision,
        "band": band,
        "net_benefit_usd": net_benefit_usd,
        "top_drivers": top_drivers,
        "explanation": explanation,
        "created_at": datetime.utcnow().isoformat(),
    }

    errors = client.insert_rows_json(config.TABLE_RECOMMENDATIONS, [row])

    if errors:
        return {"error": f"BigQuery insert failed: {errors}"}

    return {"status": "success", "run_id": run_id, "product_family": product_family}


# ──────────────────────────────────────────────
# TOOL 4: Write missing data escalation
# ──────────────────────────────────────────────

def bq_write_missing_data_escalation(
    run_id: str,
    product_family: str,
    missing_fields: list,
    reason: str,
    severity: str = "MED"
) -> dict:
    """Record missing/insufficient data. The agent must not guess."""
    row = {
        "run_id": run_id,
        "product_family": product_family,
        "missing_fields": missing_fields,
        "reason": reason,
        "severity": severity,
        "created_at": datetime.utcnow().isoformat(),
    }

    errors = client.insert_rows_json(config.TABLE_ESCALATIONS, [row])

    if errors:
        return {"error": f"BigQuery insert failed: {errors}"}

    return {"status": "escalated", "run_id": run_id, "product_family": product_family}


# ──────────────────────────────────────────────
# HELPER: List all scoreable product families
# ──────────────────────────────────────────────

def bq_list_product_families(limit: int = 50) -> list:
    """List product families with their volume and data coverage."""
    query = f"""
    SELECT
      c.product_family,
      d.annualized_volume,
      c.cases_with_repair_cost,
      c.total_rma_cases,
      ROUND(c.cases_with_repair_cost / c.total_rma_cases, 2) AS repair_data_coverage
    FROM `{config.TABLE_UNIT_COSTS}` c
    JOIN `{config.TABLE_DEMAND}` d
      ON c.product_family = d.product_family
    WHERE c.product_family != 'UNKNOWN'
    ORDER BY d.annualized_volume DESC
    LIMIT {limit}
    """
    results = client.query(query).result()
    return [dict(row) for row in results]
