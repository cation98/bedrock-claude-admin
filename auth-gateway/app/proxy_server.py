"""CONNECT 프록시 서버 — Pod에서 외부 API 접근을 중계.

별도 프로세스로 실행 (포트 3128). supervisord가 uvicorn과 함께 관리.

동작 흐름:
  1. Pod → CONNECT apis.data.go.kr:443 HTTP/1.1 + Proxy-Authorization 헤더
  2. 인증: USER_ID:POD_PROXY_SECRET → terminal_sessions.proxy_secret 검증
  3. 도메인 체크: DomainWhitelist.is_allowed(domain)
  4. 허용 → TCP 터널 수립 (양방향 릴레이)
     차단 → 403 Forbidden
  5. 비동기 접근 로그 기록 (proxy_access_logs)
"""

import asyncio
import base64
import logging
import time
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [proxy] %(levelname)s %(message)s")
logger = logging.getLogger("proxy_server")

PROXY_PORT = 3128
RELAY_BUFFER_SIZE = 65536


def _get_db_session():
    """DB 세션 생성 (프록시 프로세스 전용)."""
    from app.core.database import SessionLocal
    return SessionLocal()


def _parse_proxy_auth(header_value: str) -> tuple[str, str] | None:
    """Proxy-Authorization 헤더에서 user_id, secret 추출.

    형식: Basic base64(USER_ID:SECRET)
    """
    try:
        if not header_value.startswith("Basic "):
            return None
        encoded = header_value[6:].strip()
        decoded = base64.b64decode(encoded).decode("utf-8")
        if ":" not in decoded:
            return None
        user_id, secret = decoded.split(":", 1)
        return user_id, secret
    except Exception:
        return None


def _validate_proxy_secret(user_id: str, secret: str, db) -> bool:
    """DB에서 terminal_sessions.proxy_secret을 조회하여 검증."""
    from app.models.session import TerminalSession
    session = db.query(TerminalSession).filter(
        TerminalSession.username == user_id,
        TerminalSession.pod_status.in_(["creating", "running"]),
        TerminalSession.proxy_secret == secret,
    ).first()
    return session is not None


def _sync_log_access(user_id: str | None, domain: str, method: str, allowed: bool, response_time_ms: int):
    """동기 DB 접근 로그 기록 — run_in_executor에서 호출."""
    db = _get_db_session()
    try:
        from app.models.proxy import ProxyAccessLog
        log = ProxyAccessLog(
            user_id=user_id,
            domain=domain,
            method=method,
            allowed=allowed,
            response_time_ms=response_time_ms,
            created_at=datetime.now(timezone.utc),
        )
        db.add(log)
        db.commit()
    finally:
        db.close()


async def _log_access(user_id: str | None, domain: str, method: str, allowed: bool, response_time_ms: int):
    """비동기로 접근 로그 기록 — 스레드 풀에서 실행하여 이벤트 루프를 블로킹하지 않음."""
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, _sync_log_access, user_id, domain, method, allowed, response_time_ms
        )
    except Exception as e:
        logger.warning(f"Failed to log proxy access: {e}")


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """한 방향 데이터 릴레이 — reader에서 읽어 writer로 전달."""
    try:
        while True:
            data = await reader.read(RELAY_BUFFER_SIZE)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle_client(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter):
    """클라이언트 연결 처리 — CONNECT 요청 파싱 + 인증 + 도메인 검증 + 터널링."""
    start_time = time.monotonic()
    user_id = None
    domain = ""

    try:
        # HTTP 요청 라인 읽기: CONNECT host:port HTTP/1.1
        request_line = await asyncio.wait_for(client_reader.readline(), timeout=30.0)
        if not request_line:
            client_writer.close()
            return

        request_str = request_line.decode("utf-8", errors="replace").strip()
        parts = request_str.split()

        if len(parts) < 3 or parts[0].upper() != "CONNECT":
            client_writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            await client_writer.drain()
            client_writer.close()
            return

        # host:port 파싱
        host_port = parts[1]
        if ":" in host_port:
            domain, port_str = host_port.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                port = 443
        else:
            domain = host_port
            port = 443

        # 헤더 읽기
        headers: dict[str, str] = {}
        while True:
            line = await asyncio.wait_for(client_reader.readline(), timeout=10.0)
            if not line or line == b"\r\n" or line == b"\n":
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if ":" in decoded:
                key, value = decoded.split(":", 1)
                headers[key.strip().lower()] = value.strip()

        # 인증 검증
        auth_header = headers.get("proxy-authorization", "")
        if not auth_header:
            elapsed = int((time.monotonic() - start_time) * 1000)
            asyncio.create_task(_log_access(None, domain, "CONNECT", False, elapsed))
            client_writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                                b"Proxy-Authenticate: Basic realm=\"proxy\"\r\n\r\n")
            await client_writer.drain()
            client_writer.close()
            return

        creds = _parse_proxy_auth(auth_header)
        if not creds:
            elapsed = int((time.monotonic() - start_time) * 1000)
            asyncio.create_task(_log_access(None, domain, "CONNECT", False, elapsed))
            client_writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                                b"Proxy-Authenticate: Basic realm=\"proxy\"\r\n\r\n")
            await client_writer.drain()
            client_writer.close()
            return

        user_id, secret = creds

        # DB 검증
        db = _get_db_session()
        try:
            if not _validate_proxy_secret(user_id, secret, db):
                elapsed = int((time.monotonic() - start_time) * 1000)
                asyncio.create_task(_log_access(user_id, domain, "CONNECT", False, elapsed))
                client_writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                                    b"Proxy-Authenticate: Basic realm=\"proxy\"\r\n\r\n")
                await client_writer.drain()
                client_writer.close()
                return

            # 도메인 화이트리스트 검증
            from app.services.domain_whitelist import domain_whitelist
            if not domain_whitelist.is_allowed(domain, db):
                elapsed = int((time.monotonic() - start_time) * 1000)
                asyncio.create_task(_log_access(user_id, domain, "CONNECT", False, elapsed))
                logger.info(f"BLOCKED: {user_id} -> {domain}:{port}")
                client_writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                await client_writer.drain()
                client_writer.close()
                return
        finally:
            db.close()

        # 원격 서버에 TCP 연결 수립
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(domain, port),
                timeout=30.0,
            )
        except Exception as e:
            elapsed = int((time.monotonic() - start_time) * 1000)
            asyncio.create_task(_log_access(user_id, domain, "CONNECT", False, elapsed))
            logger.warning(f"Connection to {domain}:{port} failed: {e}")
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await client_writer.drain()
            client_writer.close()
            return

        # 200 Connection Established 응답
        client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_writer.drain()

        elapsed = int((time.monotonic() - start_time) * 1000)
        asyncio.create_task(_log_access(user_id, domain, "CONNECT", True, elapsed))
        logger.info(f"TUNNEL: {user_id} -> {domain}:{port}")

        # 양방향 릴레이 시작
        task1 = asyncio.create_task(_relay(client_reader, remote_writer))
        task2 = asyncio.create_task(_relay(remote_reader, client_writer))

        # 한쪽이 끊기면 양쪽 모두 종료
        done, pending = await asyncio.wait(
            [task1, task2],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

    except asyncio.TimeoutError:
        logger.warning(f"Timeout handling request for {domain}")
        try:
            client_writer.close()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Error handling client: {e}")
        try:
            client_writer.close()
        except Exception:
            pass


async def run_proxy():
    """프록시 서버 시작."""
    server = await asyncio.start_server(
        handle_client,
        host="0.0.0.0",
        port=PROXY_PORT,
    )
    addr = server.sockets[0].getsockname()
    logger.info(f"CONNECT proxy listening on {addr[0]}:{addr[1]}")

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(run_proxy())
