"""POST /api/v1/ai/messages/stream — the answer as server-sent events."""

import json

import pytest

from app.model import AiMessage, AiSession
from app.services.ai.agent_client import AgentReply, stream_sse
from app.services.errors import AgentUnavailable, TooManyRequests
from tests.ai_fakes import ScriptedAgent, StreamingAgent, reply

API = "/api/v1/ai/messages/stream"


@pytest.fixture
def use_agent(app):
    def install(agent):
        app.extensions["ai_agent_client"] = agent
        return agent

    return install


def events(response):
    """The stream parsed into (event name, data) pairs."""
    parsed = []
    for block in response.get_data(as_text=True).split("\n\n"):
        lines = [line for line in block.splitlines() if line]
        if not lines:
            continue
        name = next((l[len("event: ") :] for l in lines if l.startswith("event: ")), "message")
        data = next((l[len("data: ") :] for l in lines if l.startswith("data: ")), "")
        parsed.append((name, json.loads(data) if data else None))
    return parsed


def ask(client, headers, content, **body):
    return client.post(API, json={"content": content, **body}, headers=headers)


# ---------- The endpoint ----------


def test_answer_arrives_in_chunks_then_a_done_event(client, vendor_headers, use_agent):
    use_agent(StreamingAgent("INV-2026-0524 ", "is approved ", "for payment."))

    response = ask(client, vendor_headers, "Where is invoice INV-2026-0524?")

    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    parsed = events(response)
    assert [data["text"] for name, data in parsed if name == "delta"] == [
        "INV-2026-0524 ",
        "is approved ",
        "for payment.",
    ]
    [(_, done)] = [(name, data) for name, data in parsed if name == "done"]
    assert done["sessionId"]
    assert done["userMessage"]["content"] == "Where is invoice INV-2026-0524?"
    assert done["assistantMessage"]["content"] == "INV-2026-0524 is approved for payment."


def test_transcript_is_stored_once_the_answer_is_complete(app, client, vendor_headers, use_agent):
    use_agent(StreamingAgent("Paid ", "on May 30."))

    done = next(data for name, data in events(ask(client, vendor_headers, "Was it paid?")) if name == "done")

    with app.app_context():
        from app.database import db

        session = db.session.get(AiSession, __import__("uuid").UUID(done["sessionId"]))
        assert session.message_count == 2
        assert [(m.role, m.content) for m in session.messages] == [
            ("user", "Was it paid?"),
            ("assistant", "Paid on May 30."),
        ]


def test_follow_up_continues_the_same_conversation(client, vendor_headers, use_agent):
    agent = use_agent(StreamingAgent("First answer."))
    first = next(data for name, data in events(ask(client, vendor_headers, "Hello")) if name == "done")

    agent.chunks = ["Second answer."]
    second = next(
        data
        for name, data in events(ask(client, vendor_headers, "And the next?", sessionId=first["sessionId"]))
        if name == "done"
    )

    assert second["sessionId"] == first["sessionId"]
    # The agent is sent the conversation so far, and the portal's session id.
    assert agent.payload["sessionId"] == first["sessionId"]
    assert [m["role"] for m in agent.payload["history"]] == ["user", "assistant"]


def test_an_agent_that_streams_only_a_final_object_still_completes(app, client, vendor_headers, use_agent):
    """A `text/event-stream` carrying one final object and no deltas: the answer still arrives.

    The stream finishes on the view's first read, so the transcript it returns has to survive
    that StopIteration - losing it used to raise TypeError halfway through the response.
    """
    use_agent(StreamingAgent(final=reply("The whole answer at once.", model="one-shot")))

    response = ask(client, vendor_headers, "Are my documents complete?")

    assert response.status_code == 200
    parsed = events(response)
    # The one-piece answer is still sent as a delta, so a client rendering deltas shows it.
    assert [data["text"] for name, data in parsed if name == "delta"] == ["The whole answer at once."]
    [(_, done)] = [(name, data) for name, data in parsed if name == "done"]
    assert done["assistantMessage"]["content"] == "The whole answer at once."
    assert done["userMessage"]["content"] == "Are my documents complete?"
    with app.app_context():
        assert AiMessage.query.count() == 2


def test_a_non_streaming_agent_still_works(client, vendor_headers, use_agent):
    # ScriptedAgent only implements invoke(); the default stream() sends one chunk.
    use_agent(ScriptedAgent(reply("All of your invoices are paid.")))

    parsed = events(ask(client, vendor_headers, "Anything outstanding?"))

    assert [data["text"] for name, data in parsed if name == "delta"] == [
        "All of your invoices are paid."
    ]
    assert any(name == "done" for name, _ in parsed)


def test_citations_and_usage_from_the_final_object(client, vendor_headers, use_agent):
    use_agent(
        StreamingAgent(
            "See invoice ",
            "INV-2026-0524.",
            final=AgentReply(
                text="",
                citations=[{"type": "invoice", "id": "abc", "label": "INV-2026-0524"}],
                model="claude-sonnet-5",
                input_tokens=120,
                output_tokens=40,
            ),
        )
    )

    done = next(data for name, data in events(ask(client, vendor_headers, "Which invoice?")) if name == "done")

    assert done["assistantMessage"]["content"] == "See invoice INV-2026-0524."
    assert done["assistantMessage"]["citations"] == [
        {"type": "invoice", "id": "abc", "label": "INV-2026-0524"}
    ]


# ---------- Failures ----------


def test_agent_failure_before_the_first_chunk_is_a_normal_error(app, client, vendor_headers, use_agent):
    use_agent(StreamingAgent(error=AgentUnavailable("The AI assistant is unavailable right now.")))

    response = ask(client, vendor_headers, "Hello?")

    assert response.status_code == 503
    assert response.get_json()["message"] == "The AI assistant is unavailable right now."
    with app.app_context():
        from app.database import db

        assert db.session.query(AiMessage).count() == 0


def test_rate_limit_applies_to_the_stream(client, vendor_headers, use_agent):
    use_agent(StreamingAgent("ok"))
    for _ in range(10):
        # The body must be read: the message is only stored once the stream is consumed.
        events(ask(client, vendor_headers, "Hello"))

    response = ask(client, vendor_headers, "One more")

    assert response.status_code == 429


def test_blank_message_is_rejected(client, vendor_headers, use_agent):
    use_agent(StreamingAgent("ok"))
    assert ask(client, vendor_headers, "   ").status_code == 422


def test_unknown_conversation(client, vendor_headers, use_agent):
    use_agent(StreamingAgent("ok"))
    response = ask(client, vendor_headers, "Hello", sessionId="11111111-1111-4111-8111-111111111111")
    assert response.status_code == 404


def test_throttled_agent(client, vendor_headers, use_agent):
    use_agent(StreamingAgent(error=TooManyRequests("The AI assistant is busy.")))
    assert ask(client, vendor_headers, "Hello").status_code == 429


def test_preflight_allows_the_accept_header(client):
    response = client.options(
        API,
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "accept,content-type,x-vendor-id",
        },
    )

    assert response.status_code == 200
    assert "Accept" in response.headers["Access-Control-Allow-Headers"]


# ---------- Reading the agent's event stream ----------


def collect(lines, **kwargs):
    """Runs stream_sse to completion: (chunks, final reply)."""
    chunks = []
    stream = stream_sse(iter(lines), **kwargs)
    try:
        while True:
            chunks.append(next(stream))
    except StopIteration as stop:
        return chunks, stop.value


def test_reads_delta_objects_and_a_final_object():
    chunks, final = collect(
        [
            b'data: {"delta": "Invoice "}',
            b"",
            b': heartbeat',
            b'data: {"chunk": "INV-2026-0524 "}',
            b'data: {"token": "is paid."}',
            b'data: {"citations": [{"type": "invoice", "id": "abc", "label": "INV-2026-0524"}],'
            b' "usage": {"inputTokens": 10, "outputTokens": 5}, "model": "claude"}',
            b"data: [DONE]",
        ]
    )

    assert chunks == ["Invoice ", "INV-2026-0524 ", "is paid."]
    assert final.text == "Invoice INV-2026-0524 is paid."
    assert final.citations == [{"type": "invoice", "id": "abc", "label": "INV-2026-0524"}]
    assert (final.model, final.input_tokens, final.output_tokens) == ("claude", 10, 5)


def test_reads_plain_text_and_quoted_string_data_lines():
    chunks, final = collect(["data: Hello ", 'data: "there."'], fallback_model="agent")

    assert chunks == ["Hello ", "there."]
    assert final.text == "Hello there."
    assert final.model == "agent"


def test_an_agent_that_only_sends_a_final_object():
    chunks, final = collect(['data: {"reply": "The whole answer."}'])

    assert chunks == []
    assert final.text == "The whole answer."


def test_an_empty_stream_is_an_error():
    with pytest.raises(AgentUnavailable):
        collect(["data: [DONE]"])
