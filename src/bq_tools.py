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
    explanation: str,
    annualized_volume: int = 0,
    avg_replace_unit_cost: float = 0.0,
    avg_repair_cost: float = 0.0,
    per_unit_delta: float = 0.0,
    gross_annual_savings: float = 0.0,
    risk_buffer: float = 0.0,
    fixed_enablement_cost: float = 0.0,
) -> dict:
    """
    Write a recommendation to the recommendations table AND upsert the
    corresponding row into opportunity_scores so the dashboard reflects it.
    """
    # 1. Append to recommendations (audit log of every agent run)
    client.query(
        f"""
        INSERT INTO `{config.TABLE_RECOMMENDATIONS}`
        (run_id, product_family, decision, band, net_benefit_usd, top_drivers, explanation, created_at)
        VALUES (@run_id, @pf, @decision, @band, @net, @drivers, @explanation, CURRENT_TIMESTAMP())
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
            bigquery.ScalarQueryParameter("pf", "STRING", product_family),
            bigquery.ScalarQueryParameter("decision", "STRING", decision),
            bigquery.ScalarQueryParameter("band", "STRING", band),
            bigquery.ScalarQueryParameter("net", "FLOAT64", net_benefit_usd),
            bigquery.ArrayQueryParameter("drivers", "STRING", top_drivers),
            bigquery.ScalarQueryParameter("explanation", "STRING", explanation),
        ])
    ).result()
    # 2. Upsert into opportunity_scores via MERGE (delete-old + insert-new semantics)
    merge_query = f"""
    MERGE `{config.TABLE_SCORES}` T
    USING (
      SELECT
        @run_id AS run_id,
        @pf AS product_family,
        @volume AS annualized_volume,
        @replace_cost AS avg_replace_unit_cost,
        @repair_cost AS avg_repair_cost,
        @delta AS per_unit_delta,
        @gross AS gross_annual_savings,
        @risk AS risk_buffer,
        @fixed AS fixed_enablement_cost,
        @net AS net_annual_benefit,
        @band AS band,
        @decision AS decision,
        TRUE AS sufficiency_pass,
        @drivers AS top_drivers,
        CURRENT_TIMESTAMP() AS created_at
    ) S
    ON T.product_family = S.product_family
    WHEN MATCHED THEN UPDATE SET
      run_id = S.run_id,
      annualized_volume = S.annualized_volume,
      avg_replace_unit_cost = S.avg_replace_unit_cost,
      avg_repair_cost = S.avg_repair_cost,
      per_unit_delta = S.per_unit_delta,
      gross_annual_savings = S.gross_annual_savings,
      risk_buffer = S.risk_buffer,
      fixed_enablement_cost = S.fixed_enablement_cost,
      net_annual_benefit = S.net_annual_benefit,
      band = S.band,
      decision = S.decision,
      sufficiency_pass = S.sufficiency_pass,
      top_drivers = S.top_drivers,
      created_at = S.created_at
    WHEN NOT MATCHED THEN INSERT ROW
    """

    client.query(
        merge_query,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
            bigquery.ScalarQueryParameter("pf", "STRING", product_family),
            bigquery.ScalarQueryParameter("volume", "INT64", annualized_volume),
            bigquery.ScalarQueryParameter("replace_cost", "FLOAT64", avg_replace_unit_cost),
            bigquery.ScalarQueryParameter("repair_cost", "FLOAT64", avg_repair_cost),
            bigquery.ScalarQueryParameter("delta", "FLOAT64", per_unit_delta),
            bigquery.ScalarQueryParameter("gross", "FLOAT64", gross_annual_savings),
            bigquery.ScalarQueryParameter("risk", "FLOAT64", risk_buffer),
            bigquery.ScalarQueryParameter("fixed", "FLOAT64", fixed_enablement_cost),
            bigquery.ScalarQueryParameter("net", "FLOAT64", net_benefit_usd),
            bigquery.ScalarQueryParameter("band", "STRING", band),
            bigquery.ScalarQueryParameter("decision", "STRING", decision),
            bigquery.ArrayQueryParameter("drivers", "STRING", top_drivers),
        ])
    ).result()

    return {"status": "success", "run_id": run_id, "product_family": product_family, "band": band}


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
    """
    Record insufficient data AND remove any stale opportunity_scores row
    for this family so the dashboard doesn't show a zombie recommendation.
    """
    # 1. Insert escalation row
    client.query(
        f"""
        INSERT INTO `{config.TABLE_ESCALATIONS}`
        (run_id, product_family, missing_fields, reason, severity, created_at)
        VALUES (@run_id, @pf, @fields, @reason, @severity, CURRENT_TIMESTAMP())
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
            bigquery.ScalarQueryParameter("pf", "STRING", product_family),
            bigquery.ArrayQueryParameter("fields", "STRING", missing_fields),
            bigquery.ScalarQueryParameter("reason", "STRING", reason),
            bigquery.ScalarQueryParameter("severity", "STRING", severity),
        ])
    ).result()

    # 2. Remove any existing opportunity_scores row for this family
    client.query(
        f"DELETE FROM `{config.TABLE_SCORES}` WHERE product_family = @pf",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("pf", "STRING", product_family),
        ])
    ).result()

    return {"status": "escalated", "run_id": run_id, "product_family": product_family}

def bq_count_escalations() -> int:
    """Count distinct product families currently in the escalations table."""
    query = f"SELECT COUNT(DISTINCT product_family) AS n FROM `{config.TABLE_ESCALATIONS}`"
    row = next(iter(client.query(query).result()))
    return int(row["n"])

# ──────────────────────────────────────────────
# HELPER: List all scoreable product families
# ──────────────────────────────────────────────

def bq_list_product_families(limit: int = 200) -> list:
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


# ──────────────────────────────────────────────
# HELPER: Get all opportunity scores (for dashboard)
# ──────────────────────────────────────────────

def bq_get_all_scores() -> list:
    """Fetch the latest opportunity scores for the dashboard."""
    query = f"""
    SELECT
      product_family,
      annualized_volume,
      avg_replace_unit_cost,
      avg_repair_cost,
      per_unit_delta,
      gross_annual_savings,
      risk_buffer,
      net_annual_benefit,
      band,
      decision,
      sufficiency_pass,
      top_drivers,
      created_at
    FROM `{config.TABLE_SCORES}`
    ORDER BY net_annual_benefit DESC
    """
    results = client.query(query).result()
    rows = []
    for row in results:
        r = dict(row)
        # Convert datetime to string for JSON serialization
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
        rows.append(r)
    return rows
