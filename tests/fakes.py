"""A scripted stand-in for the Anthropic client, so the agent loop can be tested without a model."""
from types import SimpleNamespace as NS


def usage(inp=100, out=50, write=0, read=0):
    return NS(input_tokens=inp, output_tokens=out, cache_creation_input_tokens=write, cache_read_input_tokens=read)


def tool(id, name, **args):
    return NS(type="tool_use", id=id, name=name, input=args)


def text(t):
    return NS(type="text", text=t)


def reply(*blocks, stop="tool_use", u=None):
    return NS(stop_reason=stop, content=list(blocks), usage=u or usage())


class FakeClient:
    """client.beta.messages.create(...) returns the scripted replies in order and records every call."""

    def __init__(self, *replies, raises=None):
        self.replies, self.calls, self.raises = list(replies), [], raises
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(dict(kw, messages=list(kw.get("messages", []))))  # snapshot: the agent keeps appending
        if self.raises:
            raise self.raises
        if not self.replies:
            raise AssertionError("the agent asked for more model turns than the test scripted")
        return self.replies.pop(0)


def present(id, answer, visual_type="none", visual_result_id=None, title=None):
    args = {"answer": answer, "visual_type": visual_type}
    if visual_result_id:
        args["visual_result_id"] = visual_result_id
    if title:
        args["visual_title"] = title
    return tool(id, "present_answer", **args)
