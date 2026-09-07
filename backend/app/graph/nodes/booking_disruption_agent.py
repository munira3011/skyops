import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional, TypedDict

from langchain_core.messages import AIMessage, HumanMessage

from app.graph.state import AgentState, Booking, Flight

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_PNR_RE = re.compile(r"\b[A-Z0-9]{5,7}\b")

# rebooking_policy.md: causes classified within vs. outside airline control.
_WITHIN_AIRLINE_CONTROL = {"technical_inspection", "crew_scheduling"}

# Compensation credit (USD) for delays within airline control, by minimum delay length.
_COMPENSATION_TIERS = ((720, 500), (360, 300), (180, 150))  # >=12h/cancelled, 6-12h, 3-6h
_APPROVAL_COMPENSATION_THRESHOLD = 300  # policy: "above 300 USD" needs agent sign-off

# Not evaluated here (no partner-airline inventory or cabin-upgrade request data available):
# rebooking_policy.md also requires approval for a cabin-class change or a partner-airline
# rebooking - this node only ever proposes a same-cabin Zenith Air rebooking.


class DisruptionAssessment(TypedDict, total=False):
    eligible: bool
    cabin_class: str
    destination: str
    compensation_usd: int
    requires_approval: bool


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


def _compensation_for_delay(delay_minutes: int) -> int:
    for threshold, amount in _COMPENSATION_TIERS:
        if delay_minutes >= threshold:
            return amount
    return 0


def _assess(booking: Booking, flight: Flight) -> DisruptionAssessment:
    """Applies rebooking_policy.md's eligibility/compensation/approval rules to a booking+flight pair."""
    cancelled = flight["status"] == "cancelled"
    delayed = flight["status"] == "delayed"
    delay_minutes = flight.get("delay_minutes") or 0
    within_control = flight.get("delay_reason") in _WITHIN_AIRLINE_CONTROL

    if not (cancelled or (delayed and delay_minutes > 90)):
        return {"eligible": False}

    if not within_control:
        compensation = 0  # weather/ATC: rebooking only, no compensation credit
    elif cancelled:
        compensation = _COMPENSATION_TIERS[0][1]  # "over 12 hours or cancellation" tier
    else:
        compensation = _compensation_for_delay(delay_minutes)

    return {
        "eligible": True,
        "cabin_class": booking["cabin_class"],
        "destination": flight["destination"],
        "compensation_usd": compensation,
        "requires_approval": compensation > _APPROVAL_COMPENSATION_THRESHOLD,
    }


def _format_reply(assessment: DisruptionAssessment) -> str:
    if not assessment["eligible"]:
        return (
            "This flight doesn't currently qualify for disruption rebooking - only delays over "
            "90 minutes or cancellations are eligible."
        )

    lines = [
        f"You're eligible for free rebooking onto the next available Zenith Air flight to "
        f"{assessment['destination']} in {assessment['cabin_class']} class, at no fare difference."
    ]
    if assessment["compensation_usd"]:
        lines.append(
            f"You also qualify for a {assessment['compensation_usd']} USD travel credit "
            "(plus meal/hotel provisions where applicable)."
        )
    if assessment["requires_approval"]:
        lines.append(
            "This compensation level requires a Zenith Air agent's sign-off before it can be "
            "finalized - your request has been forwarded for review."
        )
    else:
        lines.append("This can be confirmed automatically.")
    return " ".join(lines)


def booking_disruption_agent(state: AgentState) -> dict:
    """Reads booking/flight (from state, or a PNR looked up in the latest message) and applies rebooking_policy.md; writes `pending_approval` when the proposal needs agent sign-off."""
    booking = _find_booking(state)
    if booking is None:
        reply = "I couldn't find a booking - could you share your PNR (6-character booking reference)?"
        return {"messages": [AIMessage(content=reply)]}

    flight = state.get("flight") or _flights_by_number().get(booking["flight_number"])
    if flight is None:
        reply = f"I found your booking but couldn't find flight {booking['flight_number']} in our system."
        return {"booking": booking, "messages": [AIMessage(content=reply)]}

    assessment = _assess(booking, flight)
    result: dict = {
        "booking": booking,
        "flight": flight,
        "messages": [AIMessage(content=_format_reply(assessment))],
    }

    if assessment.get("requires_approval"):
        result["pending_approval"] = {
            "pnr": booking["pnr"],
            "flight_number": flight["flight_number"],
            "compensation_usd": assessment["compensation_usd"],
            "reason": "compensation credit exceeds 300 USD - requires Zenith Air agent sign-off per rebooking_policy.md",
        }
    else:
        result["pending_approval"] = None
    return result
