import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage

from app.graph.state import AgentState, Booking, Flight

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_PNR_RE = re.compile(r"\b[A-Z0-9]{5,7}\b")

_CABIN_LABELS = {
    "economy": "Economy",
    "premium_economy": "Premium Economy",
    "business": "Business",
    "first": "First",
}


@lru_cache(maxsize=1)
def _bookings_by_pnr() -> dict[str, Booking]:
    bookings = json.loads((_DATA_DIR / "pnr.json").read_text())["bookings"]
    return {booking["pnr"]: booking for booking in bookings}


@lru_cache(maxsize=1)
def _flights_by_number() -> dict[str, Flight]:
    flights = json.loads((_DATA_DIR / "flights.json").read_text())["flights"]
    return {flight["flight_number"]: flight for flight in flights}


def _find_booking(state: AgentState) -> Optional[Booking]:
    if state.get("booking"):
        return state["booking"]
    bookings = _bookings_by_pnr()
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            for token in _PNR_RE.findall(str(message.content).upper()):
                if token in bookings:
                    return bookings[token]
    return None


def _format_flight_status(flight: Flight) -> str:
    if flight["status"] == "on_time":
        return f"on time, departing {flight['scheduled_departure']} from gate {flight['gate']}"
    if flight["status"] == "delayed":
        return f"delayed {flight['delay_minutes']} minutes due to {flight['delay_reason']}"
    return f"cancelled due to {flight['delay_reason']}"


def _format_reply(booking: Booking, flight: Optional[Flight]) -> str:
    cabin = _CABIN_LABELS.get(booking["cabin_class"], booking["cabin_class"])
    loyalty = (
        f"{booking['loyalty_tier'].title()} ({booking['loyalty_number']})"
        if booking["loyalty_tier"] != "none"
        else "Not enrolled"
    )
    special_requests = ", ".join(booking["special_requests"]) if booking["special_requests"] else "None"

    lines = [
        f"Here are your booking details for PNR {booking['pnr']}:",
        f"- Passenger: {booking['passenger_name']}",
        f"- Flight: {booking['flight_number']}" + (f" ({flight['origin']} -> {flight['destination']})" if flight else ""),
        f"- Cabin: {cabin}, Seat {booking['seat']}",
        f"- Fare type: {booking['fare_type'].title()}",
        f"- Booking status: {booking['booking_status'].title()}",
        f"- Checked baggage: {booking['baggage_checked']} bag(s)",
        f"- Loyalty: {loyalty}",
        f"- Special requests: {special_requests}",
    ]
    if flight:
        lines.append(f"\nCurrent flight status: {_format_flight_status(flight)}.")
    return "\n".join(lines)


def booking_agent(state: AgentState) -> dict:
    """Reads booking (from state, or a PNR looked up in the latest message) and reports its
    details (cabin, seat, fare, baggage, loyalty) plus current flight status - distinct from
    booking_disruption_agent, which only handles delay/cancellation eligibility and compensation."""
    booking = _find_booking(state)
    if booking is None:
        reply = "I couldn't find a booking - could you share your PNR (6-character booking reference)?"
        return {"messages": [AIMessage(content=reply)]}

    flight = state.get("flight") or _flights_by_number().get(booking["flight_number"])
    result: dict = {"booking": booking, "messages": [AIMessage(content=_format_reply(booking, flight))]}
    if flight:
        result["flight"] = flight
    return result
