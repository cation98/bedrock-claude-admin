"""bedrock_proxy._stream_bedrock 종료 시 cache token 포함 publish."""
import inspect
from app.routers import bedrock_proxy


def test_stream_bedrock_extracts_cache_tokens():
    """_stream_bedrock 소스에 cache_creation/cache_read 추출 코드 존재."""
    src = inspect.getsource(bedrock_proxy._stream_bedrock)
    assert "cache_creation_input_tokens" in src
    assert "cache_read_input_tokens" in src


def test_stream_bedrock_passes_request_id_to_publish():
    src = inspect.getsource(bedrock_proxy._stream_bedrock)
    assert "uuid" in src.lower(), "stream도 request_id (uuid) 생성해야 함"
    assert "request_id=" in src
