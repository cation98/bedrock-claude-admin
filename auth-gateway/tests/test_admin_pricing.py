"""admin.py가 pricing.py 단일 출처를 사용하는지 + 상수 제거 검증."""
from app.routers import admin
from app.core import pricing


def test_admin_no_local_input_price_constant():
    """admin.py 모듈에 INPUT_PRICE/OUTPUT_PRICE/KRW_RATE 직접 정의 없어야 함."""
    assert not hasattr(admin, "INPUT_PRICE"), \
        "admin.py에 INPUT_PRICE 잔재. pricing.PRICE_TABLE 사용해야 함"
    assert not hasattr(admin, "OUTPUT_PRICE"), \
        "admin.py에 OUTPUT_PRICE 잔재. pricing.PRICE_TABLE 사용해야 함"
    assert not hasattr(admin, "KRW_RATE"), \
        "admin.py에 KRW_RATE 잔재. pricing.KRW_RATE 사용해야 함"


def test_admin_imports_pricing():
    """admin 모듈이 pricing을 import하는지 확인 — 단일 출처 사용 신호."""
    import inspect
    src = inspect.getsource(admin)
    assert "from app.core.pricing" in src or "from app.core import pricing" in src
