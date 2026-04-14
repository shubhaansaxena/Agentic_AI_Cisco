"""
Agent orchestrator using Gemini function calling.
Gemini decides which tools to call. Tools do the actual work.
Gemini generates the explanation. That's it.
"""

from google.cloud import aiplatform
from vertexai.generative_models import (
    GenerativeModel,
    Tool,
    FunctionDeclaration,
    Content,
    Part,
)
import vertexai
import json
import uuid
import config
import bq_tools


# ──────────────────────────────────────────────
# Initialize Vertex AI
# ──────────────────────────────────────────────

vertexai.init(project=config.PROJECT_ID, location=config.LOCATION)


# ──────────────────────────────────────────────
# Tool definitions (what Gemini sees)
# ──────────────────────────────────────────────

get_context_func = FunctionDeclaration(
    name="bq_get_part_family_context",
    description="Fetch volume, cost, and repair performance data for a product family from BigQuery.",
    parameters={
        "type": "object",
        "properties": {
            "product_family": {
                "type": "string",
                "description": "The product family code (e.g., 'UCSB', 'UCSC', 'ASR9000')",
            }
        },
        "required": ["product_family"],
    },
)

score_func = FunctionDeclaration(
    name="score_net_annual_benefit",
    description="Compute deterministic net annual benefit and banding for a product family. Use the cost values returned by bq_get_part_family_context.",
    parameters={
        "type": "object",
        "properties": {
            "annualized_volume": {"type": "integer"},
            "replace_unit_cost": {"type": "number"},
            "repair_unit_cost": {"type": "number"},
            "risk_buffer_pct": {"type": "number", "description": "Default 0.10"},
            "fixed_cost": {"type": "number", "description": "Default 0.0"},
        },
        "required": ["annualized_volume", "replace_unit_cost", "repair_unit_cost"],
    },
)

write_rec_func = FunctionDeclaration(
    name="bq_write_recommendation",
    description="Write a recommendation to BigQuery. Pass ALL fields from score_net_annual_benefit plus the cost context so the dashboard can display it.",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "string"},
            "product_family": {"type": "string"},
            "decision": {"type": "string", "enum": ["REPAIR", "REPLACE", "REVIEW"]},
            "band": {"type": "string", "enum": ["GREEN", "YELLOW", "RED"]},
            "net_benefit_usd": {"type": "number"},
            "top_drivers": {"type": "array", "items": {"type": "string"}},
            "explanation": {"type": "string"},
            "annualized_volume": {"type": "integer"},
            "avg_replace_unit_cost": {"type": "number"},
            "avg_repair_cost": {"type": "number"},
            "per_unit_delta": {"type": "number"},
            "gross_annual_savings": {"type": "number"},
            "risk_buffer": {"type": "number"},
            "fixed_enablement_cost": {"type": "number"},
        },
        "required": ["run_id", "product_family", "decision", "band", "net_benefit_usd",
                     "top_drivers", "explanation", "annualized_volume",
                     "avg_replace_unit_cost", "avg_repair_cost", "per_unit_delta",
                     "gross_annual_savings", "risk_buffer"],
    },
)

write_escalation_func = FunctionDeclaration(
    name="bq_write_missing_data_escalation",
    description="Record missing or insufficient data for a product family. Use when data is not available or below thresholds.",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "string"},
            "product_family": {"type": "string"},
            "missing_fields": {
                "type": "array",
                "items": {"type": "string"},
            },
            "reason": {"type": "string"},
            "severity": {"type": "string", "enum": ["LOW", "MED", "HIGH"]},
        },
        "required": ["run_id", "product_family", "missing_fields", "reason"],
    },
)

# Bundle all tools
agent_tools = Tool(function_declarations=[
    get_context_func,
    score_func,
    write_rec_func,
    write_escalation_func,
])


# ──────────────────────────────────────────────
# System prompt
# ──────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Repair-vs-Replace Cost Optimization Agent for Cisco's RMA workflow.

YOUR JOB:
1. Fetch data for a product family using bq_get_part_family_context
2. Check data sufficiency:
   - Does the product family have enough repair cost data? (cases_with_repair_cost / total_rma_cases >= 0.30)
   - Is annualized volume above 50?
   - Are replace and repair costs available?
3. If data is INSUFFICIENT: call bq_write_missing_data_escalation and explain what's missing
4. If data is SUFFICIENT: call score_net_annual_benefit with the values from step 1
5. Call bq_write_recommendation with:
   - The scoring result fields (net_benefit_usd, band, decision, top_drivers, per_unit_delta, gross_annual_savings, risk_buffer, fixed_enablement_cost)
   - The cost context fields from step 1 (annualized_volume, avg_replace_unit_cost, avg_repair_cost)
   - A plain-English explanation

CRITICAL RULES:
- NEVER invent or estimate cost numbers. Only use values returned by tools.
- NEVER compute savings yourself. Always use score_net_annual_benefit.
- Your explanation must reference specific numbers from tool outputs.
- Use avg_replace_unit_cost for replace_unit_cost and avg_repair_cost for repair_unit_cost.
- Generate a run_id using the format "run-" followed by the product family name.

EXPLANATION FORMAT:
Write a concise 2-3 sentence explanation covering:
- The decision (REPAIR/REPLACE/REVIEW) and band (GREEN/YELLOW/RED)
- Key numbers: per-unit delta, annualized volume, net annual benefit
- Top drivers for the recommendation
"""


# ──────────────────────────────────────────────
# Tool dispatch (maps function names to actual functions)
# ──────────────────────────────────────────────

TOOL_DISPATCH = {
    "bq_get_part_family_context": bq_tools.bq_get_part_family_context,
    "score_net_annual_benefit": bq_tools.score_net_annual_benefit,
    "bq_write_recommendation": bq_tools.bq_write_recommendation,
    "bq_write_missing_data_escalation": bq_tools.bq_write_missing_data_escalation,
}


def execute_tool_call(function_call) -> dict:
    """Execute a tool call from Gemini and return the result."""
    func_name = function_call.name
    func_args = dict(function_call.args) if function_call.args else {}

    print(f"  → Calling tool: {func_name}({json.dumps(func_args, default=str)[:200]})")

    if func_name not in TOOL_DISPATCH:
        return {"error": f"Unknown tool: {func_name}"}

    try:
        result = TOOL_DISPATCH[func_name](**func_args)
        # Convert any non-serializable types
        if hasattr(result, '__iter__') and not isinstance(result, (str, dict)):
            result = list(result)
        return result
    except Exception as e:
        return {"error": f"Tool execution failed: {str(e)}"}


# ──────────────────────────────────────────────
# Main agent loop
# ──────────────────────────────────────────────

def run_agent(user_request: str) -> str:
    """
    Run the agent for a single request.
    Gemini calls tools in a loop until it has enough info to respond.
    """
    model = GenerativeModel(
        config.GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
        tools=[agent_tools],
    )

    chat = model.start_chat()

    print(f"\n{'='*60}")
    print(f"Agent request: {user_request}")
    print(f"{'='*60}")

    # Send the user's request to Gemini
    response = chat.send_message(user_request)

    # Function calling loop: keep going until Gemini responds with text
    max_iterations = 10  # safety limit
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        # Check if Gemini wants to call a function
        function_calls = []
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if part.function_call:
                    function_calls.append(part.function_call)

        # If no function calls, Gemini is done — return its text response
        if not function_calls:
            final_text = ""
            for candidate in response.candidates:
                for part in candidate.content.parts:
                    if part.text:
                        final_text += part.text
            print(f"\nAgent response:\n{final_text}")
            return final_text

        # Execute each function call and send results back to Gemini
        tool_response_parts = []
        for fc in function_calls:
            result = execute_tool_call(fc)
            tool_response_parts.append(
                Part.from_function_response(
                    name=fc.name,
                    response={"result": result},
                )
            )

        # Send tool results back to Gemini
        response = chat.send_message(Content(parts=tool_response_parts))

    return "Agent reached maximum iterations without completing."


# ──────────────────────────────────────────────
# Quick test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    # Test with a single product family
    result = run_agent("Evaluate product family UCSB for repair vs replace.")
    print("\n" + "="*60)
    print("FINAL OUTPUT:")
    print(result)
