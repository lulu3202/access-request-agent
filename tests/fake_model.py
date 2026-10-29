"""A scripted stand-in for the Anthropic client, so loop tests need no API call."""

from types import SimpleNamespace as NS


def tool(tool_name, **args):
    return NS(type="tool_use", id=f"tu-{tool_name}-{id(args)}", name=tool_name, input=args)


def text(t):
    return NS(type="text", text=t)


class FakeClient:
    """Returns one scripted response per messages.create() call."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.messages = self

    def create(self, **kwargs):
        return NS(content=self.turns.pop(0))
