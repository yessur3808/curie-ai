"""Preserve role boundaries while remaining compatible with text-only callers."""


class ChatPrompt(str):
    """A text prompt with explicitly constructed messages, never parsed user labels."""

    def __new__(cls, text, messages):
        obj = super().__new__(cls, text)
        obj._messages = tuple((m["role"], str(m["content"])) for m in messages)
        return obj

    @property
    def messages(self):
        return [{"role": role, "content": content} for role, content in self._messages]

    def with_system_guidance(self, guidance):
        messages = self.messages
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] += "\n" + guidance
        else:
            messages.insert(0, {"role": "system", "content": guidance})
        # Keep legacy completion clients readable, too.
        return ChatPrompt(str(self) + "\n" + guidance, messages)


def conversation_prompt(system, history, user):
    messages = [{"role": "system", "content": system}]
    for role, text in history:
        if role in {"user", "assistant"}:
            messages.append({"role": role, "content": str(text)})
    messages.append({"role": "user", "content": user})
    text = (
        "\n".join(m["role"].title() + ": " + m["content"] for m in messages)
        + "\nAssistant:"
    )
    return ChatPrompt(text, messages)
