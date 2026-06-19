"""Reply payloads sent by the bot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ReplyMessageType = Literal["text", "interactive"]


@dataclass(frozen=True, slots=True)
class BotReply:
    """Unified bot reply for Feishu message delivery.

    Attributes:
        msg_type: Feishu message type.
        content: JSON-serializable Feishu message content.
    """

    msg_type: ReplyMessageType
    content: dict[str, Any]

    @classmethod
    def text(cls, text: str) -> BotReply:
        """Builds a plain text reply.

        Args:
            text: Text shown to the user.

        Returns:
            Text reply payload.
        """

        return cls(msg_type="text", content={"text": text})

    @classmethod
    def interactive(cls, card: dict[str, Any]) -> BotReply:
        """Builds an interactive card reply.

        Args:
            card: Feishu interactive card content.

        Returns:
            Interactive reply payload.
        """

        return cls(msg_type="interactive", content=card)
