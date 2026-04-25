"""config.py 신규 필드 + KRW_RATE 제거 검증."""
from app.core.config import Settings
from app.core import pricing


def test_settings_has_bedrock_region_default_seoul():
    s = Settings()
    assert s.bedrock_region == "ap-northeast-2"


def test_settings_has_snapshot_loop_enabled_default_false():
    """T20 활성화 후 worker가 SSOT이므로 snapshot loop는 기본 비활성."""
    s = Settings()
    assert s.snapshot_loop_enabled is False


def test_settings_has_no_krw_rate_attribute():
    """KRW_RATE는 pricing.py로 이동. config에서 제거되어 import 에러 없음."""
    s = Settings()
    assert not hasattr(s, "krw_rate")


def test_pricing_module_owns_krw_rate():
    assert pricing.KRW_RATE == 1400
