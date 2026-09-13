"""Typed tool failures and safe conversation-facing error messages."""

from __future__ import annotations

import asyncio


class ToolExecutionError(RuntimeError):
    """An operational tool failure with a message safe to show to a user."""

    def __init__(self, message: str, *, user_message: str, retryable: bool = False):
        super().__init__(message)
        self.user_message = user_message
        self.retryable = retryable


class SandboxCommandError(ToolExecutionError):
    """A command that failed inside, or while starting, the secure sandbox."""

    def __init__(self, command: str, exit_code: int, output: str):
        detail = output.strip() or "No diagnostic output was produced."
        namespace_failure = "creating new namespace failed" in detail.casefold()
        if namespace_failure:
            user_message = (
                "The secure command sandbox could not start because the host is "
                "temporarily out of process or namespace capacity. Nothing ran and "
                "nothing is still running. Please try again in a moment."
            )
        else:
            user_message = (
                f"The {command} command stopped with exit code {exit_code}. "
                "Nothing is still running. Check the task details or logs, then try again."
            )
        super().__init__(
            f"Command {command!r} exited with {exit_code}: {detail}",
            user_message=user_message,
            retryable=namespace_failure,
        )
        self.command = command
        self.exit_code = exit_code
        self.output = detail


def user_facing_tool_error(exc: BaseException, capability: str = "tool") -> str:
    """Return a bounded error that does not leak internals or imply background work."""
    if isinstance(exc, ToolExecutionError):
        return exc.user_message
    label = capability.replace("_skill", "").replace("_", " ").strip() or "tool"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return (
            f"The {label} tool timed out before it finished. It has stopped and "
            "nothing is still running. Please try again."
        )
    if isinstance(exc, PermissionError):
        return f"The {label} tool was blocked by its safety policy, so nothing ran."
    return (
        f"The {label} tool ran into an internal error and stopped. "
        "Nothing is still running. Please try again."
    )
