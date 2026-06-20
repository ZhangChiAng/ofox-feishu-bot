"""Reply payloads sent by the bot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ReplyMessageType = Literal["text", "image"]


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
    def image(cls, png_bytes: bytes) -> BotReply:
        """Builds an image reply.

        Args:
            png_bytes: PNG image bytes to upload before sending.

        Returns:
            Image reply payload.
        """

        return cls(msg_type="image", content={"image": png_bytes})
