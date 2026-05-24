"""
Claude-powered match analyzer.

Uses Anthropic tool use to GUARANTEE structured JSON output. The model is forced
to call our `submit_match_analysis` / `submit_multi_analysis` tools, whose input
schemas define the exact shape of the response. No more fragile JSON-slicing.
"""

import json
import logging
import anthropic
from app.core.config import settings

log = logging.getLogger(__name__)


# ─── Tool schemas (force-structured output) ────────────────────────────

MATCH_ANALYSIS_TOOL = {
    "name": "submit_match_analysis",
    "description": "Submit your match analysis. You MUST call this tool with all fields populated.",
    "input_schema": {
        "type": "object",
        "properties": {
            "match": {"type": "string", "description": "e.g. 'Manchester City vs Chelsea'"},
            "sport": {"type": "string"},
            "recommendation": {
                "type": "string",
                "description": "e.g. 'Home Win', 'Draw', 'Away Win', 'BTTS Yes', 'Over 2.5', 'SKIP'",
            },
            "confidence": {
                "type": "integer", "minimum": 0, "maximum": 100,
                "description": "Your confidence in the recommendation, 0-100.",
            },
            "implied_probability": {
                "type": "number", "minimum": 0, "maximum": 1,
                "description": "Probability you assign to the recommendation winning, 0.0-1.0.",
            },
            "odds_value": {"type": "string", "enum": ["good", "fair", "poor", "unknown"]},
            "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
            "key_factors": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
            "red_flags": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
            "predicted_score": {"type": "string"},
            "safe_for_multi": {"type": "boolean"},
            "reasoning": {"type": "string", "description": "2-4 sentences explaining your pick."},
        },
        "required": [
            "match", "sport", "recommendation", "confidence", "implied_probability",
            "odds_value", "risk_level", "key_factors", "red_flags",
            "predicted_score", "safe_for_multi", "reasoning",
        ],
    },
}

MULTI_ANALYSIS_TOOL = {
    "name": "submit_multi_analysis",
    "description": "Submit your multi-bet analysis.",
    "input_schema": {
        "type": "object",
        "properties": {
            "legs_analysis": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "match": {"type": "string"},
                        "pick": {"type": "string"},
                        "odds": {"type": "number"},
                        "implied_probability": {"type": "number"},
                        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
                        "key_concern": {"type": "string"},
                    },
                    "required": ["match", "pick", "odds", "implied_probability", "confidence", "risk_level", "key_concern"],
                },
            },
            "combined_probability": {"type": "number", "minimum": 0, "maximum": 1},
            "overall_recommendation": {"type": "string", "enum": ["PLACE", "RISKY", "AVOID"]},
            "weakest_link": {"type": "string"},
            "suggestions": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        },
        "required": [
            "legs_analysis", "combined_probability",
            "overall_recommendation", "weakest_link", "suggestions",
        ],
    },
}


SYSTEM_PROMPT_MATCH = """You are a professional sports-betting analyst.

Rules:
1. Base analysis on PROVIDED DATA — never invent stats. If a field is missing, say so in red_flags but still give your best pick using whatever context exists (home advantage, league knowledge, etc.).
2. confidence is your subjective certainty (0-100). implied_probability is the 0.0-1.0 chance you assign to your recommendation.
3. safe_for_multi = true only if confidence >= 75 AND risk_level = 'low'.
4. Be honest — if a match is truly too close to call, set recommendation="SKIP" and confidence low.
5. You MUST submit your analysis via the submit_match_analysis tool — do not respond in plain text."""

SYSTEM_PROMPT_MULTI = """You are a professional sports-betting analyst grading a multi-bet/parlay.

Rules:
1. Multiply your per-leg implied_probabilities to get combined_probability.
2. PLACE only if combined_probability * combined_odds > 1.05 (positive EV with margin).
3. RISKY if combined_probability * combined_odds is between 0.95 and 1.05.
4. AVOID if combined_probability * combined_odds < 0.95.
5. You MUST submit via the submit_multi_analysis tool."""


class AIAnalyzer:
    def __init__(self):
        self.client = None
        if settings.ANTHROPIC_API_KEY:
            self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    # ─── Match analysis ───────────────────────────────────────────────

    async def analyze_match(self, match_data: dict) -> dict:
        if not self.client:
            return {"error": "Anthropic API key not configured"}

        prompt = self._build_analysis_prompt(match_data)

        try:
            message = self.client.messages.create(
                model=settings.AI_MODEL,
                max_tokens=2000,
                tools=[MATCH_ANALYSIS_TOOL],
                tool_choice={"type": "tool", "name": "submit_match_analysis"},
                system=SYSTEM_PROMPT_MATCH,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIError as e:
            log.error("Anthropic API error in analyze_match: %s", e)
            return {"error": f"AI request failed: {e}"}

        for block in message.content:
            if block.type == "tool_use" and block.name == "submit_match_analysis":
                return block.input
        log.warning("Model did not call submit_match_analysis tool. Content: %s", message.content)
        return {"error": "Model failed to return structured analysis"}

    # ─── Multi-bet analysis ───────────────────────────────────────────

    async def analyze_multi(self, legs: list[dict]) -> dict:
        if not self.client:
            return {"error": "Anthropic API key not configured"}

        combined_odds = 1.0
        for leg in legs:
            combined_odds *= leg.get("odds", 1.0)

        prompt = f"""Analyze this {len(legs)}-leg multi-bet.

LEGS:
{json.dumps(legs, indent=2)}

COMBINED ODDS (computed for you): {combined_odds:.3f}

Give per-leg analysis and the overall verdict via the submit_multi_analysis tool."""

        try:
            message = self.client.messages.create(
                model=settings.AI_MODEL,
                max_tokens=3000,
                tools=[MULTI_ANALYSIS_TOOL],
                tool_choice={"type": "tool", "name": "submit_multi_analysis"},
                system=SYSTEM_PROMPT_MULTI,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIError as e:
            log.error("Anthropic API error in analyze_multi: %s", e)
            return {"error": f"AI request failed: {e}"}

        for block in message.content:
            if block.type == "tool_use" and block.name == "submit_multi_analysis":
                result = block.input
                # Augment with deterministic math
                result["combined_odds"] = round(combined_odds, 3)
                cp = result.get("combined_probability", 0)
                result["probability_percentage"] = round(cp * 100, 1)
                result["expected_value"] = round(cp * combined_odds - 1, 3)
                return result
        return {"error": "Model failed to return structured multi-analysis"}

    # ─── Prompt builder ───────────────────────────────────────────────

    def _build_analysis_prompt(self, data: dict) -> str:
        sections = [f"Analyze this {data.get('sport', 'football')} match:\n"]
        sections.append(f"MATCH: {data.get('home_team', '?')} vs {data.get('away_team', '?')}")
        sections.append(f"VENUE: {data.get('venue', 'Unknown')} ({'Home' if data.get('is_home') else 'Away'})")
        sections.append(f"DATE: {data.get('date', 'Unknown')}")
        sections.append(f"LEAGUE: {data.get('league', 'Unknown')}")

        if data.get("home_form"):
            f = data["home_form"]
            sections.append(f"\nHOME FORM (last 5): {f.get('form', '?')}  W{f.get('wins')}-D{f.get('draws')}-L{f.get('losses')}")
        if data.get("away_form"):
            f = data["away_form"]
            sections.append(f"AWAY FORM (last 5): {f.get('form', '?')}  W{f.get('wins')}-D{f.get('draws')}-L{f.get('losses')}")

        if data.get("home_injuries"):
            sections.append(f"\nHOME INJURIES: {json.dumps(data['home_injuries'])}")
        if data.get("away_injuries"):
            sections.append(f"AWAY INJURIES: {json.dumps(data['away_injuries'])}")

        if data.get("h2h"):
            sections.append(f"\nHEAD TO HEAD (last 5): {json.dumps(data['h2h'])}")

        if data.get("standings"):
            sections.append(f"\nSTANDINGS (top 10): {json.dumps(data['standings'])}")

        if data.get("odds"):
            sections.append(f"\nMARKET ODDS (avg): {json.dumps(data['odds'])}")

        if data.get("extra_context"):
            sections.append(f"\nEXTRA CONTEXT: {data['extra_context']}")

        return "\n".join(sections)


ai_analyzer = AIAnalyzer()
