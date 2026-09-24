"""A test double for the agent service: no AWS or HTTP calls, deterministic replies."""

from dataclasses import replace

from app.services.ai.agent_client import AgentClient, AgentReply


def reply(text: str, **fields) -> AgentReply:
    return AgentReply(text=text, **fields)


class ScriptedAgent(AgentClient):
    """Returns the scripted replies in order and records every payload it was sent."""

    name = "fake-agent"

    def __init__(self, *replies: AgentReply, error: Exception | None = None):
        self.replies = list(replies)
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def invoke(self, session_id: str, payload: dict) -> AgentReply:
        self.calls.append((session_id, payload))
        if self.error:
            raise self.error
        if not self.replies:
            raise AssertionError("Unexpected extra agent call")
        return self.replies.pop(0)

    @property
    def payload(self) -> dict:
        """The last payload sent to the agent."""
        return self.calls[-1][1]


class StreamingAgent(AgentClient):
    """Streams one scripted answer as chunks, like an agent that sends `text/event-stream`."""

    name = "fake-streaming-agent"

    def __init__(self, *chunks: str, final: AgentReply | None = None, error: Exception | None = None):
        self.chunks = list(chunks)
        self.final = final
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def invoke(self, session_id: str, payload: dict) -> AgentReply:
        raise AssertionError("The streaming fake should be used through stream()")

    def stream(self, session_id: str, payload: dict):
        self.calls.append((session_id, payload))
        if self.error:
            raise self.error
        for chunk in self.chunks:
            yield chunk
        text = "".join(self.chunks)
        if self.final is None:
            return AgentReply(text=text, model=self.name)
        # Like a real stream: the final object adds citations and usage to the chunks.
        return replace(self.final, text=self.final.text or text)

    @property
    def payload(self) -> dict:
        return self.calls[-1][1]
