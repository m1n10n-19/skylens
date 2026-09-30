"""
DeepSeek analysis planner.

DeepSeek reads the customer's request and decides WHAT should be
analysed (use case, candidate type, requirements, criteria, data
needed). It never supplies measurements or scores.
"""

from analysis_spec import spec_from_llm

from use_cases import planner_catalog, resolve_use_case


PLANNER_SYSTEM = (
    "You are the SkyLens analysis planner. "
    "Return only valid JSON."
)


def build_planner_prompt(query):

    return f"""
You are the analysis planner for SkyLens.

SkyLens answers questions about physical places: buildings, land,
roads, infrastructure and changes over time. The customer describes
a decision in plain language. Your job is to decide WHAT must be
analysed. SkyLens's own data providers will do the measuring.

Customer request:

{query}

SkyLens currently has these analysis modules:

{planner_catalog()}

Return ONLY a JSON object with these fields:

{{
  "intent_type": "<one of the module ids above if one fits; otherwise
                  a new short snake_case id describing the intent>",
  "location": "<single plain-text place name searchable on a map,
               e.g. 'Thoraipakkam, Chennai, Tamil Nadu, India',
               or null>",
  "radius_km": <number ONLY if the customer stated a distance, else null>,
  "candidate_type": "<what is being evaluated, e.g. building,
                     land_parcel, site, construction_site>",
  "industry": "<e.g. solar, ev_charging, food_and_beverage, real_estate>",
  "requirements": {{
    "area": {{
      "min": <number or null>,
      "max": <number or null>,
      "target": <number or null>,
      "unit": "<sqm | sqft | cent | ground | acre | hectare | null>",
      "as_stated": "<the size exactly as the customer said it, or null>"
    }},
    "vacant": <true | false | null>,
    "building_type": "<e.g. commercial, industrial, or null>",
    "business_type": "<e.g. food court, pharmacy, or null>",
    "...": "<any other requirement the customer stated>"
  }},
  "data_needed": ["<data layers the analysis would need>"],
  "criteria": ["<what should be measured to rank candidates>"],
  "constraints": ["<hard constraints from the request>"],
  "desired_output": "<e.g. ranked_ev_charging_sites>",
  "confidence": "<high | medium | low: how sure you are about the
                 interpretation of the request>"
}}

Rules:

- Choose intent_type from the modules above whenever the request
  fits one, even if the wording differs
  ("Where should I put my next EV charger?" -> ev_charging_site_selection,
   "Where should I build a food court?" -> commercial_site_selection).
- Use common sense to infer requirements, criteria and data needed
  that the customer implied but did not say.
- Copy sizes EXACTLY as stated, with their unit. Do not convert
  units. "10 cent" -> target 10, unit "cent". "above 1 acre" -> min 1,
  unit "acre". "at least 500 square metres" -> min 500, unit "sqm".
  "4800 sq ft" -> target 4800, unit "sqft". If no size is stated,
  use null for all area values.
- Do not invent coordinates, measurements, distances, prices,
  owners or any other factual data about places.
- Use null when uncertain.
"""


def plan_from_llm_json(raw, query):
    """
    Turn DeepSeek's planner JSON into (AnalysisSpec, UseCase|None).
    """

    intent_type = raw.get("intent_type") if isinstance(raw, dict) else None

    use_case = resolve_use_case(intent_type)

    spec = spec_from_llm(
        raw,
        query,
        use_case.id if use_case else None,
    )

    if use_case and not spec.candidate_type:
        spec.candidate_type = use_case.candidate_type

    return spec, use_case
