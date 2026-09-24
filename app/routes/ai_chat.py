import json

from flask import Response, stream_with_context
from flask.views import MethodView
from flask_smorest import Blueprint

from app.extensions.ai import get_chat_service
from app.schema.ai import AiMessageCreateSchema, AiReplySchema
from app.services.current_vendor import get_current_vendor
from app.services.errors import ServiceError

# The AI endpoints here take the vendor's message and relay it to the agent, which is a
# separate service (LangChain / LangGraph on Bedrock AgentCore).
blp = Blueprint(
    "ai_chat",
    __name__,
    url_prefix="/api/v1/ai",
    description="Send a message to the AI assistant (the agent itself runs as a separate service)",
)


@blp.route("/messages")
class MessageList(MethodView):
    @blp.arguments(AiMessageCreateSchema)
    @blp.response(201, AiReplySchema)
    @blp.alt_response(404, description="Unknown conversation, or the context invoice isn't the vendor's")
    @blp.alt_response(409, description="The conversation is full; start a new one")
    @blp.alt_response(422, description="Empty or too-long message (max 4000 characters)")
    @blp.alt_response(429, description="Too many messages per minute, or the agent is throttled")
    @blp.alt_response(503, description="The agent is unreachable or not configured")
    def post(self, data):
        """Send a message to the assistant and get its answer

        Omit `sessionId` to start a conversation; the response returns the id to send with the
        next message. The agent may look up the vendor's data before answering, so a reply can
        take up to about a minute.
        """
        user_message, assistant_message, session = get_chat_service().send(
            get_current_vendor(),
            data["content"],
            session_id=data["session_id"],
            agent_id=data["agent_id"],
            context=data["context"],
        )


        print(f"DATA: {data}")
        return {
            "session_id": session.id,
            "user_message": user_message,
            "assistant_message": assistant_message,
        }


def _event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, default=str)}\n\n"


def _one_shot(stored) -> list[str]:
    """Both events for an answer that arrived whole, rendered before its rows detach.

    The text is sent as a delta as well, so a client rendering the stream sees the same shape
    whether or not the agent streamed it.
    """
    _, assistant_message, _ = stored
    return [_event("delta", {"text": assistant_message.content}), _done(stored)]


def _done(stored) -> str:
    """The closing event: the stored transcript, exactly as POST /ai/messages returns it."""
    user_message, assistant_message, session = stored
    return _event(
        "done",
        AiReplySchema().dump(
            {
                "session_id": session.id,
                "user_message": user_message,
                "assistant_message": assistant_message,
            }
        ),
    )


@blp.route("/messages/stream")
class MessageStream(MethodView):
    @blp.arguments(AiMessageCreateSchema)
    @blp.response(200, content_type="text/event-stream")
    @blp.alt_response(404, description="Unknown conversation, or the context invoice isn't the vendor's")
    @blp.alt_response(409, description="The conversation is full; start a new one")
    @blp.alt_response(422, description="Empty or too-long message (max 4000 characters)")
    @blp.alt_response(429, description="Too many messages per minute, or the agent is throttled")
    @blp.alt_response(503, description="The agent is unreachable or not configured")
    def post(self, data):
        """Send a message and receive the answer as it is written (server-sent events)

        Same request body as POST /ai/messages. The response is a `text/event-stream`:

            event: delta   data: {"text": "…"}          repeated as the answer arrives
            event: done    data: {sessionId, userMessage, assistantMessage}
            event: error   data: {"code": 503, "message": "…"}   if it fails mid-answer

        The conversation is only stored once the answer is complete, so a stream the vendor
        abandons leaves no transcript. Errors raised before the first chunk (unknown
        conversation, rate limit, agent unavailable) are returned as normal HTTP errors.
        """
        vendor = get_current_vendor()
        stream = get_chat_service().send_stream(
            vendor,
            data["content"],
            session_id=data["session_id"],
            agent_id=data["agent_id"],
            context=data["context"],
        )
        # Runs the checks and the agent call, so failures still get a real status code. An agent
        # that streams no deltas at all - the whole answer in one final object - is already
        # finished here, and its StopIteration has to be caught to keep the return value.
        first = finished = None
        try:
            first = next(stream)
        except StopIteration as stop:
            # Already finished, so the transcript was stored inside this view. Render its events
            # now: committing expired those rows, and the session is gone by the time the
            # generator below runs, leaving them detached and unreadable.
            finished = _one_shot(stop.value)

        def events():
            try:
                if finished is not None:
                    yield from finished
                    return
                yield _event("delta", {"text": first})
                while True:
                    yield _event("delta", {"text": next(stream)})
            except StopIteration as stop:
                yield _done(stop.value)
            except ServiceError as error:
                yield _event("error", {"code": error.status_code, "message": error.message})

        return Response(
            stream_with_context(events()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # Don't let a proxy buffer the answer into one lump.
                "X-Accel-Buffering": "no",
            },
        )


