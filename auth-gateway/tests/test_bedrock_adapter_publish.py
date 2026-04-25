"""bedrock_adapter — anthropic_to_openai_response가 publish를 호출하는지."""
import inspect
from unittest.mock import patch
from app.services import bedrock_adapter


def test_anthropic_to_openai_response_takes_username():
    """OnlyOffice route가 username을 전달할 수 있도록 시그니처 확장."""
    sig = inspect.signature(bedrock_adapter.anthropic_to_openai_response)
    assert "username" in sig.parameters


def test_response_function_calls_publish():
    src = inspect.getsource(bedrock_adapter.anthropic_to_openai_response)
    assert "_publish_usage_event" in src, "publish 호출 추가 필요"
    assert 'source="onlyoffice"' in src or "source='onlyoffice'" in src


@patch("app.services.bedrock_adapter._publish_usage_event")
def test_publish_called_with_cache_tokens(mock_publish):
    """Anthropic 응답의 cache 토큰이 publish에 전달되는지."""
    anthropic_resp = {
        "content": [{"type": "text", "text": "hello"}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 3,
        },
    }
    bedrock_adapter.anthropic_to_openai_response(
        anthropic_resp,
        request_model="claude-sonnet-4-6",
        username="N1102099",
    )
    assert mock_publish.called
    kwargs = mock_publish.call_args.kwargs
    assert kwargs["username"] == "N1102099"
    assert kwargs["source"] == "onlyoffice"
    assert kwargs["cache_creation_input_tokens"] == 5
    assert kwargs["cache_read_input_tokens"] == 3
    assert "request_id" in kwargs
