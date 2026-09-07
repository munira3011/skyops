import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage

from app.graph.state import AgentState, Flight

_FLIGHTS_PATH = Path(__file__).resolve().parents[2] / "data" / "flights.json"
_FLIGHT_NUMBER_RE = re.compile(r"\b[A-Z]{2}\d{3,4}\b")


@lru_cache(maxsize=1)
def _flights_by_number() -> dict[str, Flight]:
    flights = json.loads(_FLIGHTS_PATH.read_text())["flights"]
    return {flight["flight_number"]: flight for flight in flights}


def _find_flight_number(state: AgentState) -> Optional[str]:
    booking = state.get("booking")
    if booking and booking.get("flight_number"):
        return booking["flight_number"]
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            match = _FLIGHT_NUMBER_RE.search(str(message.content).upper())
            return match.group(0) if match else None
    return None


def _format_status(flight: Flight) -> str:
    route = f"{flight['flight_number']} ({flight['origin']}->{flight['destination']})"
    if flight["status"] == "on_time":
        return f"Flight {route} is on time, departing {flight['scheduled_departure']} from gate {flight['gate']}."
    if flight["status"] == "delayed":
        return (
            f"Flight {route} is delayed {flight['delay_minutes']} minutes due to "
            f"{flight['delay_reason']}. Gate: {flight['gate']}."
        )
    return f"Flight {route} is cancelled due to {flight['delay_reason']}."


def flight_status_agent(state: AgentState) -> dict:
    """Reads booking.flight_number (or a flight number in the latest user message); writes `flight` and an AIMessage with the status."""
    flight_number = _find_flight_number(state)
    if flight_number is None:
        reply = "I couldn't find a flight number to look up — could you share your PNR or flight number?"
        return {"messages": [AIMessage(content=reply)]}

    flight = _flights_by_number().get(flight_number)
    if flight is None:
        return {"messages": [AIMessage(content=f"I couldn't find flight {flight_number} in our system.")]}

    return {"flight": flight, "messages": [AIMessage(content=_format_status(flight))]}
