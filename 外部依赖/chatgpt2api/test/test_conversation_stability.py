from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.protocol import conversation
from services.protocol.conversation import (
    ConversationRequest,
    is_connection_timeout_error,
    is_tls_connection_error,
    stream_text_deltas,
)


def test_connection_closed_abruptly_is_retryable() -> None:
    assert is_tls_connection_error("Failed to perform, curl: (56) Connection closed abruptly.")
    assert is_connection_timeout_error("curl: (28) Operation timed out")


def test_text_stream_rebuilds_connection_before_first_delta() -> None:
    calls = 0

    def fake_events(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("curl: (56) Connection closed abruptly")
        yield {"type": "conversation.delta", "delta": "连接恢复"}

    first_backend = MagicMock()
    second_backend = MagicMock()
    with (
        patch("services.protocol.conversation.OpenAIBackendAPI", side_effect=[first_backend, second_backend]),
        patch("services.protocol.conversation.conversation_events", side_effect=fake_events),
        patch("services.protocol.conversation.time.sleep"),
        patch.object(conversation.account_service, "mark_text_used") as mark_text_used,
    ):
        result = list(
            stream_text_deltas(
                SimpleNamespace(access_token="token-a"),
                ConversationRequest(model="gpt-5", prompt="测试"),
            )
        )

    assert result == ["连接恢复"]
    assert calls == 2
    first_backend.close.assert_called_once_with()
    second_backend.close.assert_called_once_with()
    mark_text_used.assert_called_once_with("token-a")


def test_text_stream_does_not_replay_after_output_started() -> None:
    def fake_events(*_args, **_kwargs):
        yield {"type": "conversation.delta", "delta": "已经输出"}
        raise RuntimeError("curl: (56) Connection closed abruptly")

    active_backend = MagicMock()
    with (
        patch("services.protocol.conversation.OpenAIBackendAPI", return_value=active_backend),
        patch("services.protocol.conversation.conversation_events", side_effect=fake_events),
        patch("services.protocol.conversation.time.sleep") as sleep,
    ):
        with pytest.raises(RuntimeError, match="Connection closed abruptly"):
            list(
                stream_text_deltas(
                    SimpleNamespace(access_token="token-a"),
                    ConversationRequest(model="gpt-5", prompt="测试"),
                )
            )

    sleep.assert_not_called()
    active_backend.close.assert_called_once_with()
