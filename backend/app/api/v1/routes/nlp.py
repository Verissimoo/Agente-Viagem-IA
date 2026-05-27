"""POST /api/v1/parse-intent — converts free text in PT-BR to ParsedIntent.

Uses regex + heuristics only. LLM is restricted to the summarizer.
"""
from fastapi import APIRouter

from backend.app.api.v1.schemas.search_response import ParseIntentRequestDTO, ParseIntentResponseDTO
from backend.app.nlp.intent_parser import parse_intent_ptbr

router = APIRouter(tags=["nlp"])


@router.post("/parse-intent", response_model=ParseIntentResponseDTO)
def parse_intent(payload: ParseIntentRequestDTO) -> ParseIntentResponseDTO:
    intent = parse_intent_ptbr(payload.text)
    return ParseIntentResponseDTO(
        origin_iata=intent.origin_iata,
        destination_iata=intent.destination_iata,
        origin_city=intent.origin_city,
        destination_city=intent.destination_city,
        date_start=intent.date_start,
        date_return=intent.date_return,
        trip_type=intent.trip_type.value if intent.trip_type else "oneway",
        cabin=intent.cabin.value if intent.cabin else "economy",
        adults=intent.adults,
        direct_only=intent.direct_only,
        flex_mode=intent.flex_mode,
        flex_days=intent.flex_days,
        confidence=intent.confidence,
        notes=intent.notes,
    )
