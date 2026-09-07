import operator
from typing import Annotated, Literal, Optional

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class Flight(TypedDict):
    flight_number: str
    origin: str
    destination: str
    scheduled_departure: str
    scheduled_arrival: str
    status: Literal["on_time", "delayed", "cancelled"]
    aircraft: str
    gate: Optional[str]
    delay_minutes: Optional[int]
    delay_reason: Optional[str]


class Booking(TypedDict):
    pnr: str
    passenger_name: str
    flight_number: str
    cabin_class: Literal["economy", "premium_economy", "business", "first"]
    seat: str
    booking_status: Literal["confirmed", "waitlisted", "cancelled"]
    loyalty_number: Optional[str]
    loyalty_tier: Literal["none", "silver", "gold", "platinum"]
    fare_type: Literal["saver", "standard", "flexible"]
    baggage_checked: int
    special_requests: list[str]


class PendingApproval(TypedDict):
    pnr: str
    flight_number: str
    compensation_usd: int
    reason: str


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    booking: Optional[Booking]
    flight: Optional[Flight]
    active_agent: str
    next: Optional[str]
    handoff_reason: Optional[str]
    pending_approval: Optional[PendingApproval]
    guardrail_flags: Annotated[list[str], operator.add]
