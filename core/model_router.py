"""Automatic Opus vs Sonnet model selection — user never sees this."""

import logging
from dataclasses import dataclass

from config import settings, OPUS_TRIGGER_KEYWORDS, OPUS_MESSAGE_LENGTH_THRESHOLD, OPUS_HISTORY_TURN_THRESHOLD

logger = logging.getLogger(__name__)


@dataclass
class ModelSelection:
    model: str
    max_tokens: int
    reason: str


def select_model(message: str, history_turn_count: int = 0) -> ModelSelection:
    """
    Automatically pick claude-opus-4-5 or claude-sonnet-4-5.

    Routes to Opus when:
    - message contains any OPUS_TRIGGER_KEYWORDS
    - message length exceeds threshold
    - conversation history is long (synthesis needed)
    Returns Sonnet for everything else.
    """
    message_lower = message.lower()

    # Keyword check
    for keyword in OPUS_TRIGGER_KEYWORDS:
        if keyword in message_lower:
            return ModelSelection(
                model=settings.opus_model,
                max_tokens=settings.opus_max_tokens,
                reason=f"keyword '{keyword}' detected",
            )

    # Length check
    if len(message) > OPUS_MESSAGE_LENGTH_THRESHOLD:
        return ModelSelection(
            model=settings.opus_model,
            max_tokens=settings.opus_max_tokens,
            reason=f"message length {len(message)} > {OPUS_MESSAGE_LENGTH_THRESHOLD}",
        )

    # Long conversation needing synthesis
    if history_turn_count > OPUS_HISTORY_TURN_THRESHOLD:
        return ModelSelection(
            model=settings.opus_model,
            max_tokens=settings.opus_max_tokens,
            reason=f"conversation depth {history_turn_count} > {OPUS_HISTORY_TURN_THRESHOLD}",
        )

    return ModelSelection(
        model=settings.sonnet_model,
        max_tokens=settings.sonnet_max_tokens,
        reason="fast-path: short, simple task",
    )
