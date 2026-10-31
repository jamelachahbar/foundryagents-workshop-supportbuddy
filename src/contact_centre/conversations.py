"""Conversations owned by the contact centre, and the live updates sent to browsers.

The contact centre, not the agent, owns each conversation. That is the production pattern for a
handover: the engagement hub (here this lab app, in production for example Dynamics 365 Contact
Center) decides who answers the customer at any moment.

    agent    -> SupportBuddy answers the customer
    waiting  -> SupportBuddy handed over; the customer waits for a human advisor
    advisor  -> a human advisor talks to the customer; SupportBuddy stays silent
    closed   -> the conversation is over
"""

from __future__ import annotations

import asyncio
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from contact_centre.evidence import EvidencePanel

MODES = ("agent", "waiting", "advisor", "closed")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Conversation:
    customer_id: str | None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    mode: str = "agent"
    busy: bool = False
    advisor: str | None = None
    handover: dict[str, Any] | None = None
    # Set when SupportBuddy identifies the customer in a chat that did not sign in.
    identified: dict[str, str] | None = None
    created_at: str = field(default_factory=now)
    # What the customer and the advisor see.
    transcript: list[dict[str, Any]] = field(default_factory=list)
    # The AG-UI message history the contact centre sends to SupportBuddy (AG-UI clients own history).
    agent_history: list[dict[str, Any]] = field(default_factory=list)
    # The advisor's private thread with the assist agent; never shown to the customer.
    assist_transcript: list[dict[str, Any]] = field(default_factory=list)
    assist_history: list[dict[str, Any]] = field(default_factory=list)
    assist_busy: bool = False
    evidence: EvidencePanel | None = None

    def summary(self) -> dict[str, Any]:
        last = next((m for m in reversed(self.transcript) if m["role"] in ("customer", "agent", "advisor")), None)
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "mode": self.mode,
            "busy": self.busy,
            "advisor": self.advisor,
            "handover": self.handover,
            "identified": self.identified,
            "created_at": self.created_at,
            "last_message": last["text"][:140] if last else "",
            **({
                "evidence": self.evidence.snapshot(), "evidence_phase": self.evidence.phase,
                "trace": [event.copy() for event in self.evidence.trace],
            } if self.evidence is not None else {}),
        }

    def customer_view(self) -> dict[str, Any]:
        return {**self.summary(), "transcript": self.transcript}

    def desk_view(self) -> dict[str, Any]:
        return {**self.customer_view(), "assist": self.assist_transcript, "assist_busy": self.assist_busy}


class Bus:
    """Fan-out of live updates to the browsers listening over Server-Sent Events."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        # A buffered producer can update a message again before subscribers serialize this event.
        event = deepcopy(event)
        for queue in list(self._subscribers):
            if queue.full():
                # A browser that stopped reading must not block everyone else.
                self._subscribers.discard(queue)
            else:
                queue.put_nowait(event)


class ConversationStore:
    def __init__(self) -> None:
        self.conversations: dict[str, Conversation] = {}
        self.bus = Bus()

    def create(self, customer_id: str | None) -> Conversation:
        conversation = Conversation(customer_id=customer_id)
        self.conversations[conversation.id] = conversation
        self.publish_state(conversation)
        self.add(conversation, "system", "You are chatting with SupportBuddy, an AI assistant. You can ask for a person at any time.")
        return conversation

    def get(self, conversation_id: str) -> Conversation | None:
        return self.conversations.get(conversation_id)

    def add(self, conversation: Conversation, role: str, text: str, **extra: Any) -> dict[str, Any]:
        message = {"id": uuid.uuid4().hex, "role": role, "text": text, "at": now(), **extra}
        conversation.transcript.append(message)
        self.publish_message(conversation, message)
        return message

    def publish_message(self, conversation: Conversation, message: dict[str, Any]) -> None:
        self.bus.publish({"type": "message", "conversation": conversation.id, "message": message})

    def publish_state(self, conversation: Conversation) -> None:
        self.bus.publish({"type": "conversation", "conversation": conversation.id, "summary": conversation.summary()})

    def add_assist(self, conversation: Conversation, role: str, text: str, **extra: Any) -> dict[str, Any]:
        message = {"id": uuid.uuid4().hex, "role": role, "text": text, "at": now(), **extra}
        conversation.assist_transcript.append(message)
        self.publish_assist(conversation, message)
        return message

    def publish_assist(self, conversation: Conversation, message: dict[str, Any]) -> None:
        self.bus.publish({"type": "assist", "conversation": conversation.id, "message": message})

    def set_mode(self, conversation: Conversation, mode: str, *, advisor: str | None = None) -> None:
        if mode not in MODES:
            raise ValueError(f"Unknown mode {mode!r}")
        conversation.mode = mode
        if advisor is not None:
            conversation.advisor = advisor
        self.publish_state(conversation)
