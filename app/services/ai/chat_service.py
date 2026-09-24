"""The vendor's side of an AI conversation: store the message, ask the agent, store the answer.

The agent lives in a separate repository (LangChain / LangGraph on Bedrock AgentCore); see
app/services/ai/agent_client.py. This service owns the transcript, the per-vendor limits, and
the vendor scoping - nothing about prompts, models or tools.

A question and its answer are stored together only once the answer exists, so a failed agent
call (429/503) leaves nothing half-written and the vendor can retry.
"""

import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.model import AiMessage, AiSession, Vendor
from app.services import invoice_service
from app.services.ai.agent_client import AgentClient
from app.services.errors import Conflict, NotFound, TooManyRequests, ValidationFailed

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 4000
MAX_TITLE_CHARS = 200
DEFAULT_AGENT_ID = "assistant"
NEW_CONVERSATION = "New conversation"


@dataclass
class Turn:
    """One question, checked and ready to send to the agent. A snapshot, so a streamed answer
    doesn't depend on database objects while it arrives."""

    content: str
    session_id: uuid.UUID
    agent_id: str
    context: dict | None
    message_count: int
    history: list[dict]
    is_new: bool = False


@dataclass(frozen=True)
class ChatLimits:
    history_messages: int = 20
    max_messages_per_session: int = 100
    rate_limit_per_minute: int = 10


def _now():
    return datetime.now(timezone.utc)


def _preview(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:MAX_TITLE_CHARS]


class ChatService:
    def __init__(self, session: Session, agent: AgentClient, *, limits: ChatLimits = ChatLimits()):
        self._session = session
        self._agent = agent
        self._limits = limits

    def send(
        self,
        vendor: Vendor,
        content: str,
        *,
        session_id: uuid.UUID | None = None,
        agent_id: str | None = None,
        context: dict | None = None,
    ) -> tuple[AiMessage, AiMessage, AiSession]:
        """One message and its answer. Without a session_id, a new conversation is started."""
        stream = self.send_stream(
            vendor, content, session_id=session_id, agent_id=agent_id, context=context
        )
        try:
            while True:
                next(stream)
        except StopIteration as stop:
            return stop.value

    def send_stream(
        self,
        vendor: Vendor,
        content: str,
        *,
        session_id: uuid.UUID | None = None,
        agent_id: str | None = None,
        context: dict | None = None,
    ) -> Iterator[str]:
        """send() as a generator: yields the answer's text as the agent produces it.

        Used as `user, assistant, session = yield from service.send_stream(...)`. Nothing is
        stored until the answer is complete, so a failed or abandoned stream leaves no
        half-written transcript. The checks and limits run before the first chunk, so a caller
        can still turn them into a normal HTTP error response.
        """
        turn = self._prepare(vendor, content, session_id, agent_id, context)

        reply = yield from self._agent.stream(str(turn.session_id), self._payload(vendor, turn))

        return self._store(vendor, turn, reply)

    # ---------- Sessions ----------

    def _prepare(
        self,
        vendor: Vendor,
        content: str,
        session_id: uuid.UUID | None,
        agent_id: str | None,
        context: dict | None,
    ) -> "Turn":
        """Everything the agent call needs, checked before it is made. Writes nothing: a new
        conversation's id is generated here and only stored once the answer exists."""
        content = (content or "").strip()
        if not content:
            raise ValidationFailed("Message cannot be blank.", errors={"content": "Cannot be blank."})
        if len(content) > MAX_MESSAGE_CHARS:
            raise ValidationFailed(f"Messages are limited to {MAX_MESSAGE_CHARS} characters.")
        self._check_rate_limit(vendor)

        if session_id:
            ai_session = self._session_of(vendor, session_id)
            if ai_session.message_count + 2 > self._limits.max_messages_per_session:
                raise Conflict("This conversation is full. Please start a new one.")
            return Turn(
                content=content,
                session_id=ai_session.id,
                agent_id=ai_session.agent_id,
                context=ai_session.context,
                message_count=ai_session.message_count,
                history=self._history(ai_session),
            )

        invoice_id = (context or {}).get("invoice_id")
        stored_context = None
        if invoice_id:
            invoice = invoice_service.get_invoice(vendor, invoice_id)  # 404 unless it's this vendor's
            stored_context = {"invoiceId": str(invoice.id)}
        return Turn(
            content=content,
            session_id=uuid.uuid4(),
            # Which agent the message is for. The catalogue lives in the agent repository,
            # so this is stored and forwarded as-is.
            agent_id=(agent_id or "").strip()[:50] or DEFAULT_AGENT_ID,
            context=stored_context,
            message_count=0,
            history=[],
            is_new=True,
        )

    def _store(self, vendor: Vendor, turn: "Turn", reply) -> tuple[AiMessage, AiMessage, AiSession]:
        """Writes the question, the answer and the conversation in one transaction.

        Called only once the answer is complete, so nothing is written for a failed or
        abandoned turn. The conversation is re-read here because a streamed answer arrives
        long after _prepare()."""
        now = _now()
        ai_session = None if turn.is_new else self._session.get(AiSession, turn.session_id)
        if ai_session is None:
            ai_session = AiSession(
                id=turn.session_id,
                vendor_id=vendor.id,
                agent_id=turn.agent_id,
                title=_preview(turn.content) or NEW_CONVERSATION,
                context=turn.context,
                message_count=0,
                created_at=now,
            )
            self._session.add(ai_session)
        elif ai_session.message_count == 0 and ai_session.title == NEW_CONVERSATION:
            ai_session.title = _preview(turn.content) or ai_session.title

        user_message = AiMessage(
            session=ai_session,
            sequence=ai_session.message_count + 1,
            role="user",
            content=turn.content,
            created_at=now,
        )
        assistant_message = AiMessage(
            session=ai_session,
            sequence=ai_session.message_count + 2,
            role="assistant",
            content=reply.text,
            citations=reply.citations or None,
            model=reply.model or self._agent.name,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            tool_calls=reply.tool_calls,
            created_at=now,
        )
        self._session.add_all([user_message, assistant_message])
        ai_session.message_count += 2
        ai_session.preview = _preview(reply.text)
        ai_session.updated_at = now
        self._session.commit()
        log.info(
            "AI reply vendor=%s session=%s agent=%s in=%s out=%s tools=%s",
            vendor.id, ai_session.id, ai_session.agent_id,
            reply.input_tokens, reply.output_tokens, reply.tool_calls,
        )
        return user_message, assistant_message, ai_session

    def _session_of(self, vendor: Vendor, session_id: uuid.UUID) -> AiSession:
        ai_session = self._session.get(AiSession, session_id)
        # Another vendor's conversation is reported as missing rather than forbidden.
        if ai_session is None or ai_session.vendor_id != vendor.id:
            raise NotFound("Conversation not found.")
        return ai_session

    # ---------- Limits and the agent payload ----------

    def _check_rate_limit(self, vendor: Vendor) -> None:
        since = _now() - timedelta(minutes=1)
        recent = self._session.scalar(
            select(func.count(AiMessage.id))
            .join(AiSession, AiMessage.session_id == AiSession.id)
            .where(AiSession.vendor_id == vendor.id, AiMessage.role == "user", AiMessage.created_at >= since)
        )
        if recent >= self._limits.rate_limit_per_minute:
            raise TooManyRequests("You're sending messages too quickly. Please wait a minute and try again.")

    def _payload(self, vendor: Vendor, turn: "Turn") -> dict:
        """What the agent receives.

        `history` is the conversation before this message (the new one is `message`), sent so a
        stateless agent still has the context; one that keeps its own memory per session id can
        ignore it."""
        return {
            "message": turn.content,
            "sessionId": str(turn.session_id),
            "agentId": turn.agent_id,
            "vendor": {"id": str(vendor.id), "name": vendor.name},
            "context": turn.context or {},
            "history": turn.history,
        }

    def _history(self, ai_session: AiSession) -> list[dict]:
        messages = ai_session.messages[-self._limits.history_messages :]
        # The transcript must start with a question.
        while messages and messages[0].role != "user":
            messages = messages[1:]
        return [{"role": m.role, "content": m.content} for m in messages]
