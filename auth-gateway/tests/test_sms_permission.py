"""SMS 발송 권한 pre-check 테스트.

User.can_send_sms=False 인 사용자는 POST /api/v1/sms/send 호출 시 403.
권한 보유자는 일일 한도 없이 발송 가능해야 한다.
"""


def test_send_sms_403_when_no_permission(
    client, db_session, create_test_user, override_current_user
):
    create_test_user(username="NOPERM01", can_send_sms=False)
    override_current_user(username="NOPERM01")

    resp = client.post(
        "/api/v1/sms/send",
        json={"phone_number": "010-0000-0000", "message": "hi"},
    )
    assert resp.status_code == 403, resp.text
    assert "권한이 없습니다" in resp.json()["detail"]


def test_send_sms_ok_when_permission_granted(
    client, db_session, create_test_user, override_current_user, mock_sms_gateway
):
    create_test_user(username="OK01", can_send_sms=True)
    override_current_user(username="OK01")

    resp = client.post(
        "/api/v1/sms/send",
        json={"phone_number": "010-0000-0000", "message": "hi"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True


def test_send_sms_unlimited_for_permitted_user(
    client, db_session, create_test_user, override_current_user, mock_sms_gateway
):
    create_test_user(username="OK01", can_send_sms=True)
    override_current_user(username="OK01")

    for i in range(11):
        resp = client.post(
            "/api/v1/sms/send",
            json={"phone_number": "010-0000-0000", "message": f"msg {i}"},
        )
        assert resp.status_code == 200, f"iteration {i}: {resp.text}"


def test_send_sms_ok_via_pod_auth(
    client, db_session, create_test_user, override_current_user, mock_sms_gateway
):
    """Pod 경로(X-Pod-Name/X-Pod-Token) 로 들어와도 권한 체크가 동작해야 함."""
    create_test_user(username="N1001063", can_send_sms=True)
    override_current_user(username="N1001063", auth_type="pod")

    resp = client.post(
        "/api/v1/sms/send",
        json={"phone_number": "010-0000-0000", "message": "from pod"},
    )
    assert resp.status_code == 200, resp.text
