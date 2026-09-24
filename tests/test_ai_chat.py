import json
import uuid

import pytest

from app.services.ai.agent_client import AgentReply, parse_reply
from app.services.errors import AgentUnavailable, TooManyRequests
from tests.ai_fakes import ScriptedAgent, reply

API = "/api/v1/ai/messages"


@pytest.fixture
def use_agent(app):
    def install(agent):
        app.extensions["ai_agent_client"] = agent
        return agent

    return install


def ask(client, headers, content, **body):
    return client.post(API, json={"content": content, **body}, headers=headers)


def invoice_id(client, headers, invoice_no):
    return client.get(f"/api/v1/invoices?search={invoice_no}", headers=headers).get_json()["items"][0]["id"]


# ---------- The endpoint ----------


def test_first_message_starts_a_conversation(client, vendor_headers, use_agent):
    agent = use_agent(ScriptedAgent(reply("INV-2026-0524 is approved for payment.")))

    response = ask(client, vendor_headers, "Where is invoice INV-2026-0524?")

    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["sessionId"]
    assert body["userMessage"]["role"] == "user"
    assert body["userMessage"]["content"] == "Where is invoice INV-2026-0524?"
    assert body["assistantMessage"]["role"] == "assistant"
    assert body["assistantMessage"]["content"] == "INV-2026-0524 is approved for payment."
    assert body["assistantMessage"]["citations"] == []
    assert body["userMessage"]["createdAt"]
    assert len(agent.calls) == 1


def test_the_agent_gets_the_message_vendor_and_session(client, vendor_headers, use_agent):
    agent = use_agent(ScriptedAgent(reply("Sure.")))

    session_id = ask(client, vendor_headers, "Hello there").get_json()["sessionId"]

    sent_session_id, payload = agent.calls[0]
    assert sent_session_id == session_id
    # AgentCore requires a session id of at least 33 characters.
    assert len(sent_session_id) >= 33
    assert payload["message"] == "Hello there"
    assert payload["sessionId"] == session_id
    assert payload["agentId"] == "assistant"
    assert payload["vendor"]["id"] == vendor_headers["X-Vendor-Id"]
    assert payload["vendor"]["name"]
    assert payload["context"] == {}
    # The new message travels as "message"; "history" is the conversation before it.
    assert payload["history"] == []


def test_a_second_message_continues_the_conversation_with_history(client, vendor_headers, use_agent):
    agent = use_agent(ScriptedAgent(reply("It is approved."), reply("On 30 September.")))
    session_id = ask(client, vendor_headers, "Where is INV-2026-0524?").get_json()["sessionId"]

    response = ask(client, vendor_headers, "When will it be paid?", sessionId=session_id)

    assert response.status_code == 201
    assert response.get_json()["sessionId"] == session_id
    assert agent.payload["message"] == "When will it be paid?"
    assert agent.payload["history"] == [
        {"role": "user", "content": "Where is INV-2026-0524?"},
        {"role": "assistant", "content": "It is approved."},
    ]


def test_history_is_capped_and_starts_with_a_question(client, vendor_headers, use_agent, app):
    app.config["AI_HISTORY_MESSAGES"] = 3
    agent = use_agent(ScriptedAgent(reply("One."), reply("Two."), reply("Three.")))
    session_id = ask(client, vendor_headers, "First?").get_json()["sessionId"]
    ask(client, vendor_headers, "Second?", sessionId=session_id)

    ask(client, vendor_headers, "Third?", sessionId=session_id)

    # The last three stored messages start with the answer "One."; a history that would start
    # with an answer is trimmed to start with a question.
    assert agent.payload["history"] == [
        {"role": "user", "content": "Second?"},
        {"role": "assistant", "content": "Two."},
    ]


def test_agent_id_and_invoice_context_are_forwarded(client, vendor_headers, use_agent):
    agent = use_agent(ScriptedAgent(reply("Looks complete.")))
    an_invoice = invoice_id(client, vendor_headers, "INV-2026-0524")

    response = ask(
        client,
        vendor_headers,
        "Are my documents complete?",
        agentId="document-checker",
        context={"invoiceId": an_invoice},
    )

    assert response.status_code == 201
    assert agent.payload["agentId"] == "document-checker"
    assert agent.payload["context"] == {"invoiceId": an_invoice}


def test_citations_and_usage_are_stored(client, vendor_headers, use_agent):
    use_agent(
        ScriptedAgent(
            AgentReply(
                text="Two invoices are pending.",
                citations=[{"type": "invoice", "id": "INV-1", "label": "INV-1"}],
                model="claude-via-agentcore",
                input_tokens=120,
                output_tokens=45,
                tool_calls=2,
            )
        )
    )

    body = ask(client, vendor_headers, "What is pending?").get_json()

    assert body["assistantMessage"]["citations"] == [{"type": "invoice", "id": "INV-1", "label": "INV-1"}]


# ---------- Validation, scoping and limits ----------


def test_a_vendor_is_required(client):
    assert ask(client, {}, "Hello").status_code == 401


def test_blank_and_oversized_messages_are_rejected(client, vendor_headers, use_agent):
    use_agent(ScriptedAgent())

    assert ask(client, vendor_headers, "   ").status_code == 422
    assert ask(client, vendor_headers, "x" * 4001).status_code == 422


def test_another_vendors_conversation_is_not_found(client, vendor_headers, use_agent):
    use_agent(ScriptedAgent(reply("Hi.")))

    response = ask(client, vendor_headers, "Hello", sessionId=str(uuid.uuid4()))

    assert response.status_code == 404


def test_a_context_invoice_must_be_the_vendors(client, vendor_headers, use_agent):
    use_agent(ScriptedAgent())

    response = ask(client, vendor_headers, "Hello", context={"invoiceId": str(uuid.uuid4())})

    assert response.status_code == 404


def test_a_full_conversation_is_rejected(client, vendor_headers, use_agent, app):
    app.config["AI_MAX_MESSAGES_PER_SESSION"] = 2
    use_agent(ScriptedAgent(reply("First answer.")))
    session_id = ask(client, vendor_headers, "First?").get_json()["sessionId"]

    response = ask(client, vendor_headers, "Second?", sessionId=session_id)

    assert response.status_code == 409


def test_the_rate_limit_is_per_vendor_per_minute(client, vendor_headers, use_agent, app):
    app.config["AI_RATE_LIMIT_PER_MINUTE"] = 1
    use_agent(ScriptedAgent(reply("First answer.")))
    ask(client, vendor_headers, "First?")

    response = ask(client, vendor_headers, "Second?")

    assert response.status_code == 429


# ---------- When the agent fails ----------


@pytest.mark.parametrize(
    "error, status",
    [(AgentUnavailable("down"), 503), (TooManyRequests("busy"), 429)],
)
def test_a_failed_agent_call_stores_nothing(client, vendor_headers, use_agent, error, status):
    use_agent(ScriptedAgent(error=error))

    assert ask(client, vendor_headers, "Where is my invoice?").status_code == status

    # Nothing was written, so the same message can be retried.
    use_agent(ScriptedAgent(reply("It is approved.")))
    retry = ask(client, vendor_headers, "Where is my invoice?")
    assert retry.status_code == 201
    assert retry.get_json()["assistantMessage"]["content"] == "It is approved."


def test_an_unconfigured_agent_answers_503(client, vendor_headers):
    # No agent client is installed by the fixture, so create_app's fallback is in place.
    assert ask(client, vendor_headers, "Hello").status_code == 503


# ---------- Reading the agent's response ----------


def test_parse_reply_reads_the_documented_shape():
    body = json.dumps(
        {
            "reply": "Invoice INV-1 is approved.",
            "citations": [{"type": "invoice", "id": "abc", "label": "INV-1"}],
            "model": "claude-sonnet-5",
            "usage": {"inputTokens": 10, "outputTokens": 3},
            "toolCalls": 1,
        }
    ).encode()

    parsed = parse_reply(body)

    assert parsed.text == "Invoice INV-1 is approved."
    assert parsed.citations == [{"type": "invoice", "id": "abc", "label": "INV-1"}]
    assert parsed.model == "claude-sonnet-5"
    assert (parsed.input_tokens, parsed.output_tokens, parsed.tool_calls) == (10, 3, 1)


def test_parse_reply_accepts_a_plain_body_and_message_key():
    assert parse_reply(b"Just text").text == "Just text"
    assert parse_reply(b'{"message": "From message"}').text == "From message"
    assert parse_reply(b'{"output": "From output"}').text == "From output"


def test_parse_reply_drops_unusable_citations_and_usage():
    parsed = parse_reply(
        json.dumps(
            {
                "reply": "Here you go.",
                "citations": [{"type": "invoice"}, {"type": "nonsense", "id": "x"}, "junk",
                              {"type": "policy", "id": 7}],
                "usage": {"inputTokens": "many"},
            }
        ).encode()
    )

    assert parsed.citations == [{"type": "policy", "id": "7", "label": "7"}]
    assert parsed.input_tokens is None


def test_parse_reply_rejects_an_empty_answer():
    with pytest.raises(AgentUnavailable):
        parse_reply(b'{"reply": "   "}')


def _langgraph_state(*, question="Are my documents complete?", answer="Here is what I found.") -> bytes:
    """A LangGraph agent returning its raw state: the reply in `result`, usage on the messages.

    The thread replays earlier turns, so the first question/answer pair must not be counted.
    """
    return json.dumps(
        {
            "result": answer,
            "messages": [
                {"type": "human", "content": "An earlier question", "id": "h0"},
                {
                    "type": "ai",
                    "content": "An earlier answer",
                    "response_metadata": {"model_name": "apac.amazon.nova-lite-v1:0"},
                    "usage_metadata": {"input_tokens": 5000, "output_tokens": 300},
                },
                {"type": "human", "content": question, "id": "h1"},
                {
                    "type": "ai",
                    "content": [{"type": "tool_use", "name": "retriever"}],
                    "response_metadata": {"model_name": "apac.amazon.nova-lite-v1:0"},
                    "usage_metadata": {"input_tokens": 586, "output_tokens": 97},
                    "tool_calls": [{"name": "retriever", "args": {}, "id": "t1"}],
                },
                {"type": "tool", "content": "retrieved text", "name": "retriever", "tool_call_id": "t1"},
                {
                    "type": "ai",
                    "content": answer,
                    "response_metadata": {"model_name": "apac.amazon.nova-lite-v1:0"},
                    "usage_metadata": {"input_tokens": 5201, "output_tokens": 74},
                },
            ],
        }
    ).encode()


def test_parse_reply_reads_a_langgraph_state_dump():
    parsed = parse_reply(_langgraph_state())

    assert parsed.text == "Here is what I found."
    assert parsed.model == "apac.amazon.nova-lite-v1:0"
    # Only the two AI messages after the last question: 586 + 5201 and 97 + 74.
    assert (parsed.input_tokens, parsed.output_tokens) == (5787, 171)
    assert parsed.tool_calls == 1
    # This shape carries none, so the UI gets no source links.
    assert parsed.citations == []


def test_parse_reply_prefers_an_explicit_contract_over_the_message_list():
    body = json.loads(_langgraph_state())
    body |= {"reply": "From reply", "model": "claude-sonnet-5", "usage": {"inputTokens": 1}, "toolCalls": 0}

    parsed = parse_reply(json.dumps(body).encode())

    assert parsed.text == "From reply"
    assert parsed.model == "claude-sonnet-5"
    # An explicit 0 is kept, not treated as missing and refilled from the messages.
    assert (parsed.input_tokens, parsed.tool_calls) == (1, 0)


def test_parse_reply_ignores_a_message_list_it_cannot_use():
    parsed = parse_reply(json.dumps({"result": "Fine.", "messages": ["junk", {"type": "human"}]}).encode())

    assert parsed.text == "Fine."
    assert (parsed.model, parsed.input_tokens, parsed.output_tokens) == (None, None, None)
