import os

# GCP Project Settings
# Replace "cisco-repair-replace" with your actual GCP project ID
PROJECT_ID = "cisco-repair-replace"
LOCATION = "us-central1"

# BigQuery datasets
BQ_RAW_DATASET = "rma_raw"
BQ_CURATED_DATASET = "rma_curated"
BQ_OUTPUT_DATASET = "rma_out"

# Table names
TABLE_UNIT_COSTS = f"{PROJECT_ID}.{BQ_CURATED_DATASET}.unit_costs"
TABLE_DEMAND = f"{PROJECT_ID}.{BQ_CURATED_DATASET}.fulfillment_demand"
TABLE_PART_MASTER = f"{PROJECT_ID}.{BQ_CURATED_DATASET}.part_master_dim"
TABLE_POLICY = f"{PROJECT_ID}.{BQ_CURATED_DATASET}.policy_thresholds"
TABLE_SCORES = f"{PROJECT_ID}.{BQ_OUTPUT_DATASET}.opportunity_scores"
TABLE_RECOMMENDATIONS = f"{PROJECT_ID}.{BQ_OUTPUT_DATASET}.recommendations"
TABLE_ESCALATIONS = f"{PROJECT_ID}.{BQ_OUTPUT_DATASET}.missing_data_escalations"

# Default thresholds (overridden by policy_thresholds table)
DEFAULT_MIN_VOLUME = 50
DEFAULT_MIN_BENEFIT = 5000.0
DEFAULT_RISK_BUFFER_PCT = 0.10
DEFAULT_FIXED_COST = 0.0
DEFAULT_MIN_REPAIR_COVERAGE = 0.30

# Gemini model
GEMINI_MODEL = "gemini-2.5-flash"