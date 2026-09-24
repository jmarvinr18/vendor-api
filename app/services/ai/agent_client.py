"""The port to the AI agent, and its adapters.

The agent itself lives in a separate repository (LangChain / LangGraph) and is hosted on
Amazon Bedrock AgentCore Runtime. This API never talks to a model: it hands the vendor's
message to the agent and stores what comes back.

The agent is expected to answer with a JSON object:

    {
      "reply": "…",                                  required
      "citations": [{"type": "invoice", "id": "…", "label": "…"}],
      "model": "…",
      "usage": {"inputTokens": 0, "outputTokens": 0},
      "toolCalls": 0
    }

Everything except `reply` is optional and only stored for cost tracking and the UI's
source links. `message`, `output` and `result` are accepted in place of `reply`.

A LangGraph agent that returns its raw state instead — `{"result": "…", "messages": [...]}`
— is also read: the reply comes from `result`, and the model and token usage from the
messages after the last human one (see `_langgraph_metadata`). Such a response carries no
citations, so the UI gets no source links until the agent sends them.

Adapters map transport failures to AgentUnavailable / TooManyRequests, so callers never
handle boto3 or urllib exception types.
"""

import json
import logging
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from app.services.errors import AgentUnavailable, TooManyRequests

log = logging.getLogger(__name__)

MAX_REPLY_CHARS = 20_000
MAX_CITATIONS = 10
CITATION_TYPES = {"invoice", "document", "policy"}


@dataclass
class AgentReply:
    text: str
    citations: list[dict] = field(default_factory=list)
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: int | None = None


class AgentClient(ABC):
    """What the chat service depends on. `name` only labels stored messages."""

    name: str

    @abstractmethod
    def invoke(self, session_id: str, payload: dict) -> AgentReply:
        """Send one message to the agent. Raises AgentUnavailable or TooManyRequests."""

    def stream(self, session_id: str, payload: dict) -> Iterator[str]:
        """Yield the answer's text as it arrives; return the finished AgentReply.

        Used as `reply = yield from client.stream(...)`. The default is for agents that don't
        stream: one chunk with the whole answer, so callers need no special case.
        """
        reply = self.invoke(session_id, payload)
        yield reply.text
        return reply


def parse_reply(body: bytes | str, fallback_model: str | None = None) -> AgentReply:
    """Reads the agent's JSON response. A plain (non-JSON) body is taken as the reply text."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    try:
        data = json.loads(text)
    except ValueError:
        data = None


    print(f"DATA: {data}")

    if not isinstance(data, dict):
        # A bare string body (or a JSON string) is still a usable answer.
        reply = data if isinstance(data, str) else text
        return AgentReply(text=_clean_text(reply), model=fallback_model)

    parsed = _reply_from(data, fallback_model)
    # A complete response must carry an answer; a streamed final object need not (stream_sse).
    parsed.text = _clean_text(parsed.text)
    return parsed


def _reply_from(data: dict, fallback_model: str | None) -> AgentReply:
    """An AgentReply from the agent's JSON object, without requiring the text to be there."""
    reply = data.get("reply") or data.get("message") or data.get("output") or data.get("result") or ""
    if not isinstance(reply, str):
        reply = json.dumps(reply, ensure_ascii=False)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    # A LangGraph state dump carries no `usage`/`model`; read them off its messages instead.
    derived = _langgraph_metadata(data)
    return AgentReply(
        text=reply.strip()[:MAX_REPLY_CHARS],
        citations=_clean_citations(data.get("citations")),
        model=data.get("model") or derived.get("model") or fallback_model,
        input_tokens=_first(
            _as_int(usage.get("inputTokens")), _as_int(usage.get("input_tokens")), derived.get("input_tokens")
        ),
        output_tokens=_first(
            _as_int(usage.get("outputTokens")), _as_int(usage.get("output_tokens")), derived.get("output_tokens")
        ),
        tool_calls=_first(
            _as_int(data.get("toolCalls")), _as_int(data.get("tool_calls")), derived.get("tool_calls")
        ),
    )


def _langgraph_metadata(data: dict) -> dict:
    """Model and token usage read off a LangGraph state dump's `messages`.

    Only the messages after the last human one are counted. `messages` replays the agent's
    whole thread, so summing the list would charge earlier turns to this answer.
    """
    messages = data.get("messages")
    if not isinstance(messages, list):
        return {}

    turn: list[dict] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("type") == "human":
            turn = []
        else:
            turn.append(message)

    model, tool_calls = None, 0
    input_tokens, output_tokens, counted = 0, 0, False
    for message in turn:
        if message.get("type") == "tool":
            tool_calls += 1
            continue
        if message.get("type") != "ai":
            continue
        metadata = message.get("response_metadata")
        if isinstance(metadata, dict) and metadata.get("model_name"):
            model = metadata["model_name"]
        usage = message.get("usage_metadata")
        if isinstance(usage, dict):
            given = _as_int(usage.get("input_tokens")), _as_int(usage.get("output_tokens"))
            if any(value is not None for value in given):
                counted = True
                input_tokens += given[0] or 0
                output_tokens += given[1] or 0

    return {
        "model": model,
        "input_tokens": input_tokens if counted else None,
        "output_tokens": output_tokens if counted else None,
        "tool_calls": tool_calls if turn else None,
    }


DELTA_KEYS = ("delta", "chunk", "token", "text", "content")
FINAL_KEYS = ("reply", "message", "output", "result", "citations", "usage", "model", "toolCalls")


def stream_sse(lines: Iterable[bytes | str], fallback_model: str | None = None) -> Iterator[str]:
    """Yield text chunks from an agent's `text/event-stream`; return the finished AgentReply.

    Each `data:` line is either a piece of the answer (a bare string, or an object with
    `delta` / `chunk` / `token` / `text` / `content`) or the final object described at the top
    of this module, which carries the citations and usage. Comments, `event:` lines, heartbeats
    and `[DONE]` are ignored, so an agent that only sends plain text still works.
    """
    pieces: list[str] = []
    final: AgentReply | None = None

    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        # Only the line ending is stripped: spaces inside a chunk are part of the answer.
        line = line.rstrip("\r\n")
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[len("data:") :]
        # One optional space after the colon belongs to the protocol, not the data.
        data = data[1:] if data.startswith(" ") else data
        if not data.strip() or data.strip() == "[DONE]":
            continue

        try:
            parsed = json.loads(data)
        except ValueError:
            pieces.append(data)
            yield data
            continue

        if isinstance(parsed, str):
            pieces.append(parsed)
            yield parsed
        elif isinstance(parsed, dict):
            if any(key in parsed for key in FINAL_KEYS):
                final = _reply_from(parsed, fallback_model)
                continue
            piece = next(
                (parsed[key] for key in DELTA_KEYS if isinstance(parsed.get(key), str) and parsed[key]),
                "",
            )
            if piece:
                pieces.append(piece)
                yield piece

    # The deltas are the answer; the final object only adds citations and usage. An agent that
    # sends no deltas at all (just the final object) still gives us its text.
    text = "".join(pieces) or (final.text if final else "")
    return AgentReply(
        text=_clean_text(text),
        citations=final.citations if final else [],
        model=(final.model if final else None) or fallback_model,
        input_tokens=final.input_tokens if final else None,
        output_tokens=final.output_tokens if final else None,
        tool_calls=final.tool_calls if final else None,
    )


def _first(*values):
    """The first value that was actually given; 0 is a value, None is not."""
    return next((value for value in values if value is not None), None)


def _clean_text(reply: str) -> str:
    reply = (reply or "").strip()
    if not reply:
        raise AgentUnavailable("The AI assistant returned an empty answer. Please try again.")
    return reply[:MAX_REPLY_CHARS]


def _clean_citations(citations) -> list[dict]:
    if not isinstance(citations, list):
        return []
    cleaned = []
    for citation in citations[:MAX_CITATIONS]:
        if not isinstance(citation, dict):
            continue
        kind, id_, label = citation.get("type"), citation.get("id"), citation.get("label")
        if kind in CITATION_TYPES and id_:
            cleaned.append({"type": kind, "id": str(id_)[:100], "label": str(label or id_)[:200]})
    return cleaned


def _as_int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# ---------- Amazon Bedrock AgentCore Runtime ----------


class AgentCoreClient(AgentClient):
    """Invokes the agent deployed to Bedrock AgentCore Runtime.

    The conversation id is passed as `runtimeSessionId`, so AgentCore routes every message in
    one conversation to the same runtime session and the agent's own memory / LangGraph
    checkpointer can key on it.
    """

    def __init__(self, client, runtime_arn: str, *, qualifier: str = "DEFAULT"):
        self._client = client
        self._arn = runtime_arn
        self._qualifier = qualifier
        self.name = runtime_arn.rsplit("/", 1)[-1]

    def invoke(self, session_id: str, payload: dict) -> AgentReply:
        from botocore.exceptions import BotoCoreError, ClientError



        try:
            response = self._client.invoke_agent_runtime(
                agentRuntimeArn=self._arn,
                qualifier=self._qualifier,
                # AgentCore requires at least 33 characters; a UUID string is 36.
                runtimeSessionId=session_id,
                contentType="application/json",
                accept="application/json",
                payload=json.dumps(payload, default=str).encode(),
            )
            body = response["response"].read()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("ThrottlingException", "TooManyRequestsException"):
                raise TooManyRequests("The AI assistant is busy. Please try again in a minute.") from exc
            log.error("AgentCore invoke failed (%s): %s", code, exc)
            raise AgentUnavailable("The AI assistant is unavailable right now. Please try again.") from exc
        except BotoCoreError as exc:
            log.warning("AgentCore invoke failed: %s", exc)
            raise AgentUnavailable("The AI assistant is unavailable right now. Please try again.") from exc

        return parse_reply(body, fallback_model=self.name)

    def stream(self, session_id: str, payload: dict) -> Iterator[str]:
        """Asks AgentCore for `text/event-stream`. An agent that answers in one piece still
        works: the body is then read as a normal response."""
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            response = self._client.invoke_agent_runtime(
                agentRuntimeArn=self._arn,
                qualifier=self._qualifier,
                runtimeSessionId=session_id,
                contentType="application/json",
                accept="text/event-stream",
                payload=json.dumps(payload, default=str).encode(),
            )
            body = response["response"]
            if "text/event-stream" not in (response.get("contentType") or ""):
                reply = parse_reply(body.read(), fallback_model=self.name)
                yield reply.text
                return reply
            return (yield from stream_sse(body.iter_lines(), fallback_model=self.name))
        except ClientError as exc:
            raise self._transport_error(exc) from exc
        except BotoCoreError as exc:
            log.warning("AgentCore stream failed: %s", exc)
            raise AgentUnavailable("The AI assistant is unavailable right now. Please try again.") from exc

    def _transport_error(self, exc) -> Exception:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("ThrottlingException", "TooManyRequestsException"):
            return TooManyRequests("The AI assistant is busy. Please try again in a minute.")
        log.error("AgentCore invoke failed (%s): %s", code, exc)
        return AgentUnavailable("The AI assistant is unavailable right now. Please try again.")


# ---------- Plain HTTP ----------


class HttpAgentClient(AgentClient):
    """POSTs to an HTTP endpoint: the agent repository running locally, or behind a load balancer."""

    def __init__(self, url: str, *, timeout: float = 75, token: str | None = None):
        self._url = url
        self._timeout = timeout
        self._token = token
        self.name = "http-agent"

    def invoke(self, session_id: str, payload: dict) -> AgentReply:
        headers = {"Content-Type": "application/json", "X-Session-Id": session_id}

        print(f"URL: {self._url}")
        print(f"SESSION ID: {session_id}")
        print(f"PAYLOAD: {payload}")


        print(f"TOKEN: {self._token}")

        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(
            self._url, data=json.dumps(payload, default=str).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise TooManyRequests("The AI assistant is busy. Please try again in a minute.") from exc
            log.error("Agent returned %s: %s", exc.code, exc.read()[:500])
            raise AgentUnavailable("The AI assistant is unavailable right now. Please try again.") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            log.warning("Agent request failed: %s", exc)
            raise AgentUnavailable("The AI assistant is unavailable right now. Please try again.") from exc


        print(f"BODY: {body}")
        return parse_reply(body, fallback_model=self.name)

    def stream(self, session_id: str, payload: dict) -> Iterator[str]:
        """Asks the endpoint for `text/event-stream`; falls back to reading the whole body
        when it answers with anything else."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Session-Id": session_id,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(
            self._url, data=json.dumps(payload, default=str).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                if response.headers.get_content_type() != "text/event-stream":
                    reply = parse_reply(response.read(), fallback_model=self.name)
                    yield reply.text
                    return reply
                return (yield from stream_sse(response, fallback_model=self.name))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise TooManyRequests("The AI assistant is busy. Please try again in a minute.") from exc
            log.error("Agent returned %s: %s", exc.code, exc.read()[:500])
            raise AgentUnavailable("The AI assistant is unavailable right now. Please try again.") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            log.warning("Agent stream failed: %s", exc)
            raise AgentUnavailable("The AI assistant is unavailable right now. Please try again.") from exc


class UnconfiguredAgent(AgentClient):
    """Stand-in when no agent is configured: every message fails with a clear 503."""

    name = "unconfigured"

    def __init__(self, reason: str):
        self._reason = reason

    def invoke(self, session_id: str, payload: dict) -> AgentReply:
        raise AgentUnavailable(self._reason)
