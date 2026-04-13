#!/usr/bin/env python3
"""
파일 업로드/다운로드 서버 (stdlib only)

기능:
  - 디렉토리 브라우징 + 파일 다운로드
  - 드래그&드롭 / 버튼 클릭 파일 업로드
  - 업로드 파일은 uploads/ 디렉토리에 저장
  - 최대 파일 크기 제한 (기본 100MB)

사용:
  python3 fileserver.py [--port 8080] [--dir /home/node/workspace]
"""

import os
import sys
import html
import json
import re
import shutil
import subprocess
import argparse
import urllib.parse
import cgi
import datetime
import functools
import mimetypes
import unicodedata
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB


class FileServerHandler(SimpleHTTPRequestHandler):
    """파일 업로드를 지원하는 HTTP 핸들러."""

    def do_DELETE(self):
        """파일 삭제 처리 (DELETE /delete?file=filename)."""
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/delete":
            self.send_error(404, "Not Found")
            return

        params = urllib.parse.parse_qs(parsed.query)
        filename = params.get("file", [None])[0]
        if not filename:
            self.send_error(400, "Missing file parameter")
            return

        # 안전 처리: uploads 디렉토리 내 파일만 삭제 허용
        safe_name = Path(filename).name
        filepath = Path(self.directory) / "uploads" / safe_name
        if not filepath.exists() or not filepath.is_file():
            self.send_error(404, "File not found")
            return

        try:
            filepath.unlink()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"deleted": safe_name}).encode())
        except OSError as e:
            self.send_error(500, str(e))

    def do_POST(self):
        """파일 업로드 및 웹앱 관리 API (POST)."""
        parsed = urllib.parse.urlparse(self.path)

        # --- Webapp reverse proxy ---
        if parsed.path.startswith('/webapp/'):
            self._handle_webapp_proxy(parsed)
            return

        # --- Webapp management API (POST) ---
        if parsed.path == '/api/apps/start':
            self._handle_apps_start()
            return
        if parsed.path == '/api/apps/stop':
            self._handle_apps_stop()
            return
        if parsed.path == '/api/apps/stop-all':
            self._handle_apps_stop_all()
            return
        if parsed.path == '/api/apps/rename':
            self._handle_apps_rename()
            return
        if parsed.path == '/api/apps/delete-project':
            self._handle_apps_delete_project()
            return
        if parsed.path == '/api/apps/prepare-deploy':
            self._handle_apps_prepare_deploy()
            return
        if parsed.path.startswith('/api/apps/versions/') and parsed.path.endswith('/label'):
            self._handle_apps_version_label(parsed)
            return
        if parsed.path == '/api/rename':
            self._handle_rename()
            return
        if parsed.path == '/api/delete':
            self._handle_delete()
            return
        if parsed.path == '/api/mkdir':
            self._handle_mkdir()
            return
        # --- End webapp management API ---

        if parsed.path != "/upload":
            self.send_error(404, "Not Found")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_UPLOAD_SIZE:
            self.send_error(413, f"File too large (max {MAX_UPLOAD_SIZE // 1024 // 1024}MB)")
            return

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_error(400, "Expected multipart/form-data")
            return

        # Parse multipart form data
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(content_length),
            },
        )

        upload_dir = Path(self.directory) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)

        uploaded = []
        converted = []
        items = form["files"] if "files" in form else []
        if not isinstance(items, list):
            items = [items]

        for item in items:
            if not item.filename:
                continue
            # 파일명 안전 처리 (경로 탐색 방지)
            safe_name = Path(item.filename).name
            if not safe_name or safe_name.startswith("."):
                continue

            dest = upload_dir / safe_name
            # 동일 파일명 존재 시 번호 추가
            counter = 1
            stem, suffix = dest.stem, dest.suffix
            while dest.exists():
                dest = upload_dir / f"{stem}_{counter}{suffix}"
                counter += 1

            with open(dest, "wb") as f:
                f.write(item.file.read())
            uploaded.append(safe_name)

            # 10MB 초과 Excel/CSV → SQLite 자동 변환
            sqlite_result = self._auto_convert_to_sqlite(str(dest))
            if sqlite_result:
                # shared-data 디렉토리에 복사
                shared_dir = os.path.join(self.directory, 'shared-data')
                os.makedirs(shared_dir, exist_ok=True)
                shutil.copy2(sqlite_result, os.path.join(shared_dir, os.path.basename(sqlite_result)))
                converted.append(os.path.basename(sqlite_result))
                # 스키마 자동 생성 — Claude가 DB 구조를 인식할 수 있도록
                self._generate_schema_md(
                    os.path.join(shared_dir, os.path.basename(sqlite_result))
                )

        # JSON 응답
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        response = {
            "uploaded": uploaded,
            "count": len(uploaded),
            "directory": "uploads/",
        }
        if converted:
            response["converted_sqlite"] = converted
        self.wfile.write(json.dumps(response).encode())

    def do_GET(self):
        """디렉토리 리스팅 시 업로드 UI 포함, /portal은 허브 페이지, /api/files는 JSON."""
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/" or parsed.path == "":
            # Hub Ingress rewrite: /hub/{pod_name}/ → / → portal로 리다이렉트
            self._send_portal_page()
            return
        if parsed.path == "/portal" or parsed.path == "/portal/":
            self._send_portal_page()
            return
        if parsed.path.startswith("/api/browse"):
            self._send_file_listing_json(parsed)
            return
        if parsed.path == "/api/download":
            self._handle_download(parsed)
            return
        if parsed.path.startswith("/static/"):
            self._serve_static(parsed.path)
            return

        # --- Webapp reverse proxy ---
        if parsed.path.startswith('/webapp/'):
            self._handle_webapp_proxy(parsed)
            return

        # --- Webapp management API (GET) ---
        if parsed.path == '/api/apps/status':
            self._handle_apps_status()
            return
        if parsed.path.startswith('/api/apps/versions/'):
            self._handle_apps_versions(parsed)
            return
        if parsed.path == '/api/skills/local':
            self._handle_skills_local()
            return
        # --- End webapp management API ---

        path = self.translate_path(self.path)
        if os.path.isdir(path):
            self._send_directory_page(path)
            return
        super().do_GET()

    def _send_file_listing_json(self, parsed):
        """파일 브라우저용 JSON API — /api/browse?path=uploads"""
        params = urllib.parse.parse_qs(parsed.query)
        rel_path = params.get("path", [""])[0]

        # 경로 탈출 방지
        if ".." in rel_path:
            self._send_json(400, {"error": "invalid path"})
            return

        target = os.path.join(self.directory, rel_path) if rel_path else self.directory
        if not os.path.isdir(target) or not os.path.realpath(target).startswith(os.path.realpath(self.directory)):
            self._send_json(404, {"error": "not found"})
            return

        entries = []
        try:
            for name in sorted(os.listdir(target)):
                full = os.path.join(target, name)
                if name.startswith("."):
                    continue
                entry_path = os.path.join(rel_path, name) if rel_path else name
                stat = os.stat(full)
                mtime = datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
                if os.path.isdir(full):
                    entries.append({"name": name, "path": entry_path, "type": "dir", "size": 0, "mtime": mtime, "extension": ""})
                else:
                    _, ext = os.path.splitext(name)
                    entries.append({"name": name, "path": entry_path, "type": "file", "size": stat.st_size, "mtime": mtime, "extension": ext})
        except OSError:
            pass

        self._send_json(200, {"path": rel_path, "entries": entries})

    def _handle_download(self, parsed):
        """파일 다운로드 API — GET /api/download?path=relpath"""
        params = urllib.parse.parse_qs(parsed.query)
        rel_path = params.get("path", [""])[0]

        if not rel_path or ".." in rel_path:
            self._send_json(400, {"error": "invalid path"})
            return

        target = os.path.join(self.directory, rel_path)
        real_target = os.path.realpath(target)
        if not real_target.startswith(os.path.realpath(self.directory)):
            self._send_json(403, {"error": "access denied"})
            return

        # 한글 파일명 NFC/NFD 호환: 파일이 없으면 다른 정규화 형식으로 재시도
        if not os.path.exists(real_target):
            for form in ("NFC", "NFD"):
                alt = os.path.realpath(os.path.join(self.directory, unicodedata.normalize(form, rel_path)))
                if os.path.exists(alt) and alt.startswith(os.path.realpath(self.directory)):
                    real_target = alt
                    break

        if not os.path.isfile(real_target):
            self._send_json(404, {"error": "file not found"})
            return

        try:
            content_type, _ = mimetypes.guess_type(real_target)
            if content_type is None:
                content_type = "application/octet-stream"
            file_size = os.path.getsize(real_target)
            file_name = os.path.basename(real_target)

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(file_size))
            # RFC 5987: 한글 등 non-ASCII 파일명은 filename*=UTF-8'' 형식 사용
            try:
                file_name.encode("latin-1")
                self.send_header("Content-Disposition", f'attachment; filename="{file_name}"')
            except UnicodeEncodeError:
                encoded_name = urllib.parse.quote(file_name)
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{encoded_name}")
            self.end_headers()

            with open(real_target, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
        except OSError as e:
            self.send_error(500, str(e))

    def _handle_rename(self):
        """파일/디렉토리 이름 변경 API — POST /api/rename  body: {"old_path": "...", "new_name": "..."}"""
        try:
            body = self._read_body()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"error": "invalid JSON body"})
            return

        old_path = data.get("old_path", "")
        new_name = data.get("new_name", "")

        if not old_path or not new_name:
            self._send_json(400, {"error": "old_path and new_name are required"})
            return

        if ".." in old_path or ".." in new_name or "/" in new_name or "\\" in new_name:
            self._send_json(400, {"error": "invalid path or name"})
            return

        target = os.path.join(self.directory, old_path)
        real_target = os.path.realpath(target)
        if not real_target.startswith(os.path.realpath(self.directory)):
            self._send_json(403, {"error": "access denied"})
            return

        if not os.path.exists(real_target):
            self._send_json(404, {"error": "file or directory not found"})
            return

        new_target = os.path.join(os.path.dirname(real_target), new_name)
        real_new_target = os.path.realpath(new_target)
        if not real_new_target.startswith(os.path.realpath(self.directory)):
            self._send_json(403, {"error": "access denied"})
            return

        if os.path.exists(new_target):
            self._send_json(409, {"error": "target name already exists"})
            return

        try:
            os.rename(real_target, new_target)
            self._send_json(200, {"success": True})
        except OSError as e:
            self._send_json(500, {"error": str(e)})

    def _handle_delete(self):
        """파일/디렉토리 삭제 API — POST /api/delete  body: {"path": "relative/path"}"""
        try:
            body = self._read_body()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"error": "invalid JSON body"})
            return
        rel_path = data.get("path", "")
        if not rel_path or ".." in rel_path:
            self._send_json(400, {"error": "invalid path"})
            return
        target = os.path.join(self.directory, rel_path)
        real_target = os.path.realpath(target)
        if not real_target.startswith(os.path.realpath(self.directory)):
            self._send_json(403, {"error": "access denied"})
            return
        if not os.path.exists(real_target):
            self._send_json(404, {"error": "not found"})
            return
        try:
            if os.path.isdir(real_target):
                shutil.rmtree(real_target)
            else:
                os.unlink(real_target)
            self._send_json(200, {"success": True})
        except OSError as e:
            self._send_json(500, {"error": str(e)})

    def _handle_mkdir(self):
        """디렉토리 생성 API — POST /api/mkdir  body: {"path": "relative/path"}"""
        try:
            body = self._read_body()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"error": "invalid JSON body"})
            return
        rel_path = data.get("path", "")
        if not rel_path or ".." in rel_path:
            self._send_json(400, {"error": "invalid path"})
            return
        target = os.path.join(self.directory, rel_path)
        real_target = os.path.realpath(target)
        if not real_target.startswith(os.path.realpath(self.directory)):
            self._send_json(403, {"error": "access denied"})
            return
        if os.path.exists(real_target):
            self._send_json(409, {"error": "already exists"})
            return
        try:
            os.makedirs(real_target, exist_ok=True)
            self._send_json(200, {"success": True})
        except OSError as e:
            self._send_json(500, {"error": str(e)})

    STATIC_DIR = "/opt/static"

    def _serve_static(self, url_path):
        """Serve bundled static assets from /opt/static/ (Tabulator etc.)."""
        filename = os.path.basename(url_path)
        filepath = os.path.join(self.STATIC_DIR, filename)
        if not os.path.isfile(filepath):
            self.send_error(404, "Not Found")
            return
        content_type, _ = mimetypes.guess_type(filepath)
        if content_type is None:
            content_type = "application/octet-stream"
        try:
            size = os.path.getsize(filepath)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "public, max-age=604800")
            self.end_headers()
            with open(filepath, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
        except OSError as e:
            self.send_error(500, str(e))

    def _send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        """Read request body."""
        content_length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(content_length).decode('utf-8')

    # ── Webapp Helpers ──────────────────────────────────────────────

    @staticmethod
    def _kill_port(port):
        """포트를 사용하는 프로세스를 /proc에서 찾아서 종료."""
        import signal
        # 1단계: /proc/net/tcp에서 포트의 소켓 inode 찾기
        target_inode = None
        port_hex = f':{port:04X}'
        try:
            with open('/proc/net/tcp') as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) > 9 and parts[1].endswith(port_hex) and parts[3] == '0A':
                        target_inode = parts[9]
                        break
        except Exception:
            return False
        if not target_inode:
            return False
        # 2단계: 해당 inode를 가진 PID 찾기
        socket_ref = f'socket:[{target_inode}]'
        for pid_dir in os.listdir('/proc'):
            if not pid_dir.isdigit():
                continue
            try:
                for fd in os.listdir(f'/proc/{pid_dir}/fd'):
                    try:
                        if os.readlink(f'/proc/{pid_dir}/fd/{fd}') == socket_ref:
                            os.kill(int(pid_dir), signal.SIGTERM)
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    @staticmethod
    def _scan_listening_ports():
        """3000-3100 범위의 리스닝 포트를 /proc/net/tcp에서 스캔."""
        ports = {}
        try:
            with open('/proc/net/tcp') as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) < 4 or parts[3] != '0A':
                        continue
                    addr = parts[1]
                    port = int(addr.split(':')[1], 16)
                    if 3000 <= port <= 3100:
                        inode = parts[9] if len(parts) > 9 else None
                        pid = None
                        if inode:
                            for pid_dir in os.listdir('/proc'):
                                if not pid_dir.isdigit():
                                    continue
                                try:
                                    for fd in os.listdir(f'/proc/{pid_dir}/fd'):
                                        try:
                                            link = os.readlink(f'/proc/{pid_dir}/fd/{fd}')
                                            if f'socket:[{inode}]' in link:
                                                pid = int(pid_dir)
                                                break
                                        except Exception:
                                            continue
                                    if pid:
                                        break
                                except Exception:
                                    continue
                        cwd = None
                        cmd = None
                        if pid:
                            try:
                                cwd = os.readlink(f'/proc/{pid}/cwd')
                                with open(f'/proc/{pid}/cmdline') as cf:
                                    cmd = cf.read().split('\x00')[0]
                            except Exception:
                                pass
                        ports[port] = {"port": port, "pid": pid, "command": cmd, "cwd": cwd}
        except Exception:
            pass
        return ports

    # ── Webapp Reverse Proxy ───────────────────────────────────────

    def _handle_webapp_proxy(self, parsed):
        """GET/POST /webapp/{port}/* → localhost:{port}/* 리버스 프록시."""
        import http.client

        parts = parsed.path.split('/', 3)  # ['', 'webapp', '{port}', '{path}']
        if len(parts) < 3 or not parts[2].isdigit():
            self._send_json(400, {"error": "잘못된 웹앱 경로입니다. /webapp/{port}/ 형식을 사용하세요."})
            return

        port = int(parts[2])
        if not (3000 <= port <= 3100):
            self._send_json(400, {"error": "포트는 3000-3100 범위만 허용됩니다."})
            return

        target_path = '/' + parts[3] if len(parts) > 3 else '/'
        if parsed.query:
            target_path += '?' + parsed.query

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ('host', 'connection')}
        headers['Host'] = f'localhost:{port}'

        try:
            conn = http.client.HTTPConnection('localhost', port, timeout=30)
            conn.request(self.command, target_path, body=body, headers=headers)
            resp = conn.getresponse()

            self.send_response(resp.status)
            for key, val in resp.getheaders():
                if key.lower() not in ('transfer-encoding', 'connection'):
                    self.send_header(key, val)
            self.end_headers()

            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)

            conn.close()
        except ConnectionRefusedError:
            self._send_json(503, {"error": f"포트 {port}에서 실행 중인 앱이 없습니다."})
        except Exception as e:
            self._send_json(502, {"error": f"프록시 오류: {str(e)}"})

    # ── Webapp Management API Handlers ──────────────────────────────

    def _handle_apps_status(self):
        """GET /api/apps/status — Combined running apps + registered apps + projects."""
        # 1. Read webapp registry
        registry = {}
        reg_path = os.path.join(self.directory, '.webapp-registry.json')
        if os.path.exists(reg_path):
            with open(reg_path) as f:
                registry = json.load(f)

        # 2. Scan listening ports 3000-3100
        port_map = self._scan_listening_ports()
        running = list(port_map.values())

        # 3. Scan workspace projects
        projects = []
        workspace = self.directory
        for d in os.listdir(workspace):
            path = os.path.join(workspace, d)
            if not os.path.isdir(path) or d.startswith('.'):
                continue
            proj_type = None
            if os.path.isfile(os.path.join(path, 'package.json')):
                proj_type = 'node'
            elif os.path.isfile(os.path.join(path, 'requirements.txt')):
                proj_type = 'python'
            elif os.path.isfile(os.path.join(path, 'Dockerfile')):
                proj_type = 'docker'
            if proj_type:
                projects.append({"name": d, "path": path, "type": proj_type})

        # 4. Merge: registry + running + projects (auto-register detected projects)
        for proj in projects:
            if proj['name'] not in registry:
                registry[proj['name']] = {
                    "port": None, "path": proj['path'], "type": proj['type'],
                    "auto_detected": True
                }
        with open(reg_path, 'w') as f:
            json.dump(registry, f, indent=2, default=str)

        # Build response: CWD 기반으로 실행 중인 앱 매칭
        # running 프로세스의 cwd → port 매핑
        cwd_to_port = {}
        for r in running:
            if r.get('cwd'):
                cwd_to_port[r['cwd']] = r['port']

        apps = []
        registry_changed = False
        for name, info in registry.items():
            app_path = info.get('path', '')
            # CWD 매칭으로 실제 실행 중인지 확인
            matched_port = cwd_to_port.get(app_path)
            is_running = matched_port is not None
            actual_port = matched_port if is_running else None
            # 레지스트리 포트를 실제 상태와 동기화
            if is_running and info.get('port') != matched_port:
                info['port'] = matched_port
                registry_changed = True
            elif not is_running and info.get('port') is not None:
                info['port'] = None
                registry_changed = True
            app_entry = {
                "name": name, "path": app_path,
                "type": info.get('type', 'unknown'), "port": actual_port,
                "running": is_running, "auto_detected": info.get('auto_detected', False)
            }
            apps.append(app_entry)

        if registry_changed:
            with open(reg_path, 'w') as f:
                json.dump(registry, f, indent=2, default=str)

        self._send_json(200, {"apps": apps, "running_ports": running, "projects": projects})

    def _handle_apps_versions(self, parsed):
        """GET /api/apps/versions/{app_name} — Git tag based version list."""
        parts = parsed.path.split('/')
        app_name = parts[4] if len(parts) > 4 else ''

        reg_path = os.path.join(self.directory, '.webapp-registry.json')
        registry = {}
        if os.path.exists(reg_path):
            with open(reg_path) as f:
                registry = json.load(f)
        app_info = registry.get(app_name, {})
        app_path = app_info.get('path', os.path.join(self.directory, app_name))

        versions = []
        if os.path.isdir(os.path.join(app_path, '.git')):
            try:
                result = subprocess.run(
                    ['git', '-C', app_path, 'tag', '-l', 'v-*', '--sort=-creatordate',
                     '--format=%(refname:short)|%(creatordate:iso)'],
                    capture_output=True, text=True, timeout=10
                )
                for line in result.stdout.strip().split('\n'):
                    if '|' in line:
                        tag, date = line.split('|', 1)
                        versions.append({"version": tag, "date": date.strip()})
            except Exception:
                pass

        # Load labels from .webapp-versions.json
        labels_path = os.path.join(self.directory, '.webapp-versions.json')
        labels = {}
        if os.path.exists(labels_path):
            with open(labels_path) as f:
                labels = json.load(f)
        app_labels = labels.get(app_name, {})
        for v in versions:
            v['label'] = app_labels.get(v['version'], '')

        if versions:
            versions[0]['is_current'] = True

        self._send_json(200, {"versions": versions})

    def _handle_skills_local(self):
        """GET /api/skills/local — Scan .claude/skills/ directory."""
        skills_dir = os.path.expanduser('~/.claude/skills')
        skills = []
        if os.path.isdir(skills_dir):
            for d in os.listdir(skills_dir):
                skill_path = os.path.join(skills_dir, d)
                skill_md = os.path.join(skill_path, 'SKILL.md')
                if os.path.isdir(skill_path) and os.path.exists(skill_md):
                    with open(skill_md) as f:
                        content = f.read(500)
                    name = d
                    description = ''
                    lines = content.split('\n')
                    for line in lines:
                        if line.startswith('description:'):
                            description = line.split(':', 1)[1].strip()
                        if line.startswith('name:'):
                            name = line.split(':', 1)[1].strip()
                    skills.append({"dir_name": d, "name": name, "description": description, "path": skill_path})
        self._send_json(200, {"skills": skills})

    def _handle_apps_start(self):
        """POST /api/apps/start — Start a dev server."""
        body = self._read_body()
        data = json.loads(body)
        app_name = data.get('name') or os.path.basename(data.get('path', ''))
        if not app_name:
            self._send_json(400, {"error": "앱 이름 또는 경로가 필요합니다"})
            return
        app_path = data.get('path', os.path.join(self.directory, app_name))
        app_type = data.get('type', 'python')

        # Safety: only allow execution within workspace
        real_app_path = os.path.realpath(app_path)
        real_workspace = os.path.realpath(self.directory)
        if not real_app_path.startswith(real_workspace + os.sep):
            self._send_json(403, {"error": "workspace 외부 경로에서는 실행할 수 없습니다"})
            return

        reg_path = os.path.join(self.directory, '.webapp-registry.json')
        registry = {}
        if os.path.exists(reg_path):
            with open(reg_path) as f:
                registry = json.load(f)

        # _scan_listening_ports() + CWD matching = single source of truth
        port_map = self._scan_listening_ports()
        listening_ports = set(port_map.keys())
        cwd_to_port = {v['cwd']: v['port'] for v in port_map.values() if v.get('cwd')}

        # If app is already running (CWD match), kill it and reuse the same port
        existing_running_port = cwd_to_port.get(app_path)
        if existing_running_port:
            self._kill_port(existing_running_port)
            port = existing_running_port
        else:
            # Find first available port — only check actually listening ports
            used = set(listening_ports)
            port = None
            for p in range(3000, 3101):
                if p not in used:
                    port = p
                    break
            if port is None:
                self._send_json(503, {"error": "사용 가능한 포트가 없습니다"})
                return

        entrypoint = registry.get(app_name, {}).get('entrypoint')
        if not entrypoint:
            entrypoint = 'main:app' if os.path.isfile(os.path.join(app_path, 'main.py')) else 'app:app'

        env = os.environ.copy()
        env['PORT'] = str(port)
        if app_type == 'node':
            cmd = ['npm', 'start']
            pkg_json = os.path.join(app_path, 'package.json')
            if os.path.exists(pkg_json):
                with open(pkg_json) as f:
                    pkg = json.load(f)
                if 'dev' in pkg.get('scripts', {}):
                    cmd = ['npm', 'run', 'dev']
        elif app_type == 'python':
            cmd = ['python3', '-m', 'uvicorn', entrypoint, '--host', '0.0.0.0', '--port', str(port)]
        else:
            self._send_json(400, {"error": f"지원하지 않는 앱 유형: {app_type}"})
            return

        old = registry.get(app_name, {})
        registry[app_name] = {
            "port": port, "path": app_path, "type": app_type,
            "entrypoint": entrypoint if app_type == 'python' else old.get('entrypoint'),
            "auto_detected": old.get('auto_detected', False)
        }
        with open(reg_path, 'w') as f:
            json.dump(registry, f, indent=2, default=str)

        subprocess.Popen(cmd, cwd=app_path, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self._send_json(200, {"started": True, "name": app_name, "port": port})

    def _handle_apps_stop(self):
        """POST /api/apps/stop — Stop a specific app by port."""
        body = self._read_body()
        data = json.loads(body)
        port = data.get('port')
        if not isinstance(port, int) or not (3000 <= port <= 3100):
            self._send_json(400, {"error": "포트는 3000-3100 범위만 허용됩니다"})
            return
        if port:
            self._kill_port(port)
        self._send_json(200, {"stopped": True, "port": port})

    def _handle_apps_stop_all(self):
        """POST /api/apps/stop-all — Stop all apps on ports 3000-3100."""
        stopped = []
        listening = self._scan_listening_ports()
        for port in listening:
            if self._kill_port(port):
                stopped.append(port)
        self._send_json(200, {"stopped": stopped})

    def _handle_apps_rename(self):
        """POST /api/apps/rename — Rename app in registry."""
        body = self._read_body()
        data = json.loads(body)
        old_name = data['old_name']
        new_name = data['new_name']
        reg_path = os.path.join(self.directory, '.webapp-registry.json')
        registry = {}
        if os.path.exists(reg_path):
            with open(reg_path) as f:
                registry = json.load(f)
        if old_name in registry:
            registry[new_name] = registry.pop(old_name)
            with open(reg_path, 'w') as f:
                json.dump(registry, f, indent=2, default=str)
        self._send_json(200, {"renamed": True, "old": old_name, "new": new_name})

    def _handle_apps_delete_project(self):
        """POST /api/apps/delete-project — Delete app directory and registry entry."""
        body = self._read_body()
        data = json.loads(body)
        app_name = data.get('name') or os.path.basename(data.get('path', ''))
        app_path = data.get('path', os.path.join(self.directory, app_name))

        # Safety check
        real_app_path = os.path.realpath(app_path)
        real_workspace = os.path.realpath(self.directory)
        if not real_app_path.startswith(real_workspace + os.sep):
            self._send_json(403, {"error": "workspace 외부 경로는 삭제할 수 없습니다"})
            return

        reg_path = os.path.join(self.directory, '.webapp-registry.json')
        registry = {}
        if os.path.exists(reg_path):
            with open(reg_path) as f:
                registry = json.load(f)

        # Stop running app (ignore errors)
        if app_name in registry and registry[app_name].get('port'):
            try:
                self._kill_port(registry[app_name]['port'])
            except Exception:
                pass

        # Delete directory
        try:
            if os.path.isdir(app_path):
                shutil.rmtree(app_path)
        except Exception as e:
            self._send_json(500, {"error": f"디렉토리 삭제 실패: {str(e)}"})
            return

        # Remove from registry
        registry.pop(app_name, None)
        try:
            with open(reg_path, 'w') as f:
                json.dump(registry, f, indent=2, default=str)
        except Exception:
            pass

        self._send_json(200, {"deleted": True, "name": app_name})

    def _handle_apps_prepare_deploy(self):
        """POST /api/apps/prepare-deploy — 앱 코드를 deployed 경로로 복사."""
        body = self._read_body()
        data = json.loads(body)
        app_name = data.get('name', '')
        app_path = data.get('path', '')

        # app_name 안전성 검사 (path traversal 방지)
        if not app_name or '..' in app_name or '/' in app_name or '\\' in app_name:
            self._send_json(400, {"error": "유효하지 않은 앱 이름입니다"})
            return

        if not app_path:
            self._send_json(400, {"error": "앱 이름과 경로가 필요합니다"})
            return

        # 소스 검증
        real_path = os.path.realpath(app_path)
        real_workspace = os.path.realpath(self.directory)
        if not real_path.startswith(real_workspace + os.sep):
            self._send_json(403, {"error": "workspace 외부 경로는 배포할 수 없습니다"})
            return
        if not os.path.isdir(app_path):
            self._send_json(404, {"error": f"앱 디렉토리를 찾을 수 없습니다"})
            return

        # 대상 경로: ~/workspace/deployed/{app_name}/current/
        deploy_base = os.path.join(self.directory, 'deployed', app_name)
        deploy_current = os.path.join(deploy_base, 'current')

        try:
            if os.path.exists(deploy_current):
                shutil.rmtree(deploy_current)
            os.makedirs(deploy_current, exist_ok=True)

            skip_dirs = {'node_modules', '__pycache__', '.git', '.venv', 'venv', '.cache'}
            for item in os.listdir(app_path):
                if item in skip_dirs:
                    continue
                src = os.path.join(app_path, item)
                dst = os.path.join(deploy_current, item)
                if os.path.isdir(src):
                    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*skip_dirs))
                else:
                    shutil.copy2(src, dst)

            self._send_json(200, {"prepared": True, "name": app_name, "deploy_path": deploy_current})
        except Exception as e:
            self._send_json(500, {"error": f"코드 복사 실패: {str(e)}"})

    def _handle_apps_version_label(self, parsed):
        """POST /api/apps/versions/{app}/label — Update version label."""
        parts = parsed.path.split('/')
        app_name = parts[4]
        body = self._read_body()
        data = json.loads(body)
        version = data['version']
        label = data['label']

        labels_path = os.path.join(self.directory, '.webapp-versions.json')
        labels = {}
        if os.path.exists(labels_path):
            with open(labels_path) as f:
                labels = json.load(f)
        if app_name not in labels:
            labels[app_name] = {}
        labels[app_name][version] = label
        with open(labels_path, 'w') as f:
            json.dump(labels, f, indent=2, ensure_ascii=False)

        self._send_json(200, {"updated": True})

    # ── End Webapp Management API Handlers ──────────────────────────

    def _send_portal_page(self):
        """터미널 + 파일 관리 포탈 페이지."""
        user_name = os.environ.get("USER_DISPLAY_NAME", "사용자")
        user_id = os.environ.get("USER_ID", "")
        pod_name = os.environ.get("HOSTNAME", "claude-terminal")

        body = PORTAL_TEMPLATE.format(
            user_name=html.escape(user_name),
            user_id=html.escape(user_id),
            pod_name=html.escape(pod_name),
        )
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_directory_page(self, dirpath):
        """업로드 폼이 포함된 디렉토리 페이지."""
        rel_path = os.path.relpath(dirpath, self.directory)
        if rel_path == ".":
            rel_path = ""

        entries = []
        try:
            items = sorted(os.listdir(dirpath))
        except OSError:
            self.send_error(403, "Permission denied")
            return

        is_uploads = rel_path == "uploads"
        for name in items:
            fullpath = os.path.join(dirpath, name)
            display = html.escape(name)
            link = urllib.parse.quote(name)
            if os.path.isdir(fullpath):
                display += "/"
                link += "/"
                size = "-"
                delete_btn = ""
            else:
                size_bytes = os.path.getsize(fullpath)
                size = self._format_size(size_bytes)
                if is_uploads:
                    import base64
                    b64name = base64.b64encode(name.encode('utf-8')).decode('ascii')
                    delete_btn = f'<button class="del-btn" onclick="deleteFile(atob(\'{b64name}\'))">삭제</button>'
                else:
                    delete_btn = ""
            entries.append(f'<tr><td><a href="{link}">{display}</a></td><td>{size}</td><td>{delete_btn}</td></tr>')

        # 상위 디렉토리 링크
        parent = ""
        if rel_path:
            parent = '<tr><td><a href="..">..</a></td><td>-</td></tr>'

        title = f"/{rel_path}" if rel_path else "/"
        body = PAGE_TEMPLATE.format(
            title=html.escape(title),
            parent=parent,
            entries="\n".join(entries),
        )

        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    @staticmethod
    def _format_size(size):
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"

    def _auto_convert_to_sqlite(self, filepath):
        """10MB 초과 Excel/CSV → SQLite 자동 변환."""
        size = os.path.getsize(filepath)
        ext = os.path.splitext(filepath)[1].lower()

        if size < 10 * 1024 * 1024:  # 10MB 미만은 변환 불필요
            return None

        if ext not in ('.xlsx', '.xls', '.csv'):
            return None

        sqlite_path = os.path.splitext(filepath)[0] + '.sqlite'

        try:
            # Python 스크립트로 변환 실행 (별도 프로세스, 메인 서버 블로킹 방지)
            script = '''
import pandas as pd
import sqlite3
import sys

filepath = sys.argv[1]
sqlite_path = sys.argv[2]
ext = sys.argv[3]

conn = sqlite3.connect(sqlite_path)
if ext in ('.xlsx', '.xls'):
    # 모든 시트 변환
    xls = pd.ExcelFile(filepath)
    for sheet in xls.sheet_names:
        df = pd.read_excel(filepath, sheet_name=sheet)
        table_name = sheet.replace(' ', '_').lower()
        df.to_sql(table_name, conn, if_exists='replace', index=False)
elif ext == '.csv':
    df = pd.read_csv(filepath)
    df.to_sql('data', conn, if_exists='replace', index=False)
conn.close()
print('OK')
'''
            result = subprocess.run(
                ['python3', '-c', script, filepath, sqlite_path, ext],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                return sqlite_path
            return None
        except Exception:
            return None

    def _generate_schema_md(self, sqlite_path):
        """SQLite 스키마 파일 생성 — Claude가 DB 구조를 인식하는 메타데이터.

        {name}.schema.md 파일을 생성하여 테이블명, 컬럼명, 행 수, 샘플 데이터를 기록.
        Claude는 이 파일을 읽고 적절한 SQL 쿼리를 작성할 수 있다.
        """
        try:
            script = '''
import sqlite3, sys, os
from datetime import datetime

db_path = sys.argv[1]
schema_path = os.path.splitext(db_path)[0] + '.schema.md'
db_name = os.path.basename(db_path)

conn = sqlite3.connect(db_path)
cur = conn.cursor()

tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()

lines = []
lines.append(f"# {db_name}")
lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
lines.append(f"Tables: {len(tables)}")
lines.append("")

for (tbl,) in tables:
    row_count = cur.execute(f"SELECT COUNT(*) FROM [{tbl}]").fetchone()[0]
    lines.append(f"## {tbl} ({row_count:,} rows)")
    lines.append("")
    lines.append("| Column | Type | Sample |")
    lines.append("|--------|------|--------|")

    cols = cur.execute(f"PRAGMA table_info([{tbl}])").fetchall()
    sample = cur.execute(f"SELECT * FROM [{tbl}] LIMIT 1").fetchone()

    for i, col in enumerate(cols):
        col_name = col[1]
        col_type = col[2] or "TEXT"
        sample_val = str(sample[i])[:30] if sample and sample[i] is not None else "-"
        lines.append(f"| {col_name} | {col_type} | {sample_val} |")
    lines.append("")

conn.close()

with open(schema_path, "w", encoding="utf-8") as f:
    f.write("\\n".join(lines))
print("OK")
'''
            subprocess.run(
                ['python3', '-c', script, sqlite_path],
                capture_output=True, text=True, timeout=30
            )
        except Exception:
            pass  # 스키마 생성 실패해도 SQLite 자체는 유지


PORTAL_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Code — {user_name}</title>
<link rel="stylesheet" href="/hub/{pod_name}/static/tabulator_midnight.min.css">
<script src="/hub/{pod_name}/static/tabulator.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0d1117; color: #e6edf3; min-height: 100vh;
         display: flex; flex-direction: column; align-items: center; }}

  .container {{ max-width: 720px; width: 90%; padding: 32px 0; }}

  /* Header */
  .header {{ text-align: center; margin-bottom: 40px; }}
  .header h1 {{ font-size: 1.6rem; font-weight: 600; margin-bottom: 6px; }}
  .header h1 .accent {{ color: #58a6ff; }}
  .header p {{ color: #8b949e; font-size: 0.9rem; }}

  /* Cards */
  .cards {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 32px; }}
  .card {{
    background: #161b22; border: 1px solid #30363d; border-radius: 12px;
    padding: 28px 24px; text-align: center; text-decoration: none; color: inherit;
    transition: border-color 0.2s, transform 0.15s, box-shadow 0.2s;
    display: flex; flex-direction: column; align-items: center; gap: 12px;
  }}
  .card:hover {{ border-color: #58a6ff; transform: translateY(-2px);
    box-shadow: 0 4px 20px rgba(88,166,255,0.1); }}
  .card .icon {{ font-size: 2.5rem; }}
  .card h2 {{ font-size: 1.1rem; font-weight: 600; }}
  .card p {{ font-size: 0.82rem; color: #8b949e; line-height: 1.5; }}
  .card .badge {{
    display: inline-block; padding: 3px 10px; border-radius: 12px;
    font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
  }}
  .badge-blue {{ background: #1f3a5f; color: #58a6ff; }}
  .badge-green {{ background: #1a3a2a; color: #3fb950; }}

  /* Guide */
  .guide {{ background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 20px 24px; }}
  .guide h3 {{ font-size: 0.9rem; color: #8b949e; margin-bottom: 12px; font-weight: 500; }}
  .guide-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
  .guide-item {{ font-size: 0.82rem; padding: 6px 0; }}
  .guide-item code {{
    background: #21262d; padding: 2px 6px; border-radius: 4px; font-size: 0.78rem;
    color: #79c0ff;
  }}
  .guide-item .label {{ color: #8b949e; }}

  .logout-btn {{
    display: inline-block; margin-top: 10px; padding: 6px 18px;
    border: 1px solid #30363d; border-radius: 6px; color: #8b949e;
    text-decoration: none; font-size: 0.8rem; transition: all 0.2s;
  }}
  .logout-btn:hover {{ border-color: #da3633; color: #da3633; }}

  /* App sections */
  .app-section {{ background: #161b22; border: 1px solid #30363d; border-radius: 12px;
    padding: 20px 24px; margin-top: 16px; }}
  .app-section h3 {{ font-size: 0.95rem; color: #e6edf3; margin-bottom: 14px; font-weight: 600; }}
  .app-section h3 .count {{ color: #8b949e; font-weight: 400; font-size: 0.82rem; }}
  .app-list {{ list-style: none; }}
  .app-item {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 12px; border-radius: 8px; margin-bottom: 6px;
    border: 1px solid #21262d; transition: border-color 0.2s;
  }}
  .app-item:hover {{ border-color: #30363d; }}
  .app-item .app-info {{ flex: 1; }}
  .app-item .app-name {{ font-weight: 600; font-size: 0.9rem; color: #58a6ff; text-decoration: none; }}
  .app-item .app-name:hover {{ text-decoration: underline; }}
  .app-item .app-meta {{ font-size: 0.75rem; color: #8b949e; margin-top: 2px; }}
  .app-item .app-actions {{ display: flex; gap: 6px; }}
  .btn-sm {{
    padding: 4px 10px; border-radius: 4px; font-size: 0.72rem; border: 1px solid #30363d;
    background: transparent; color: #8b949e; cursor: pointer; transition: all 0.2s;
  }}
  .btn-sm:hover {{ border-color: #58a6ff; color: #58a6ff; }}
  .btn-sm.danger:hover {{ border-color: #da3633; color: #da3633; }}
  .empty-msg {{ color: #484f58; font-size: 0.82rem; text-align: center; padding: 16px 0; }}

  /* Data share item path */
  .data-path {{ font-family: 'SFMono-Regular', Consolas, monospace; font-size: 0.72rem;
    color: #7ee787; background: #0d1117; padding: 2px 6px; border-radius: 3px; }}

  /* ACL Modal */
  .modal-overlay {{
    display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    background: rgba(0,0,0,0.6); z-index: 200; align-items: center; justify-content: center;
  }}
  .modal-overlay.active {{ display: flex; }}
  .modal {{
    background: #161b22; border: 1px solid #30363d; border-radius: 12px;
    width: 420px; max-height: 80vh; overflow-y: auto; padding: 24px;
  }}
  .modal h3 {{ margin-bottom: 16px; font-size: 1rem; }}
  .modal .search-box {{
    display: flex; gap: 8px; margin-bottom: 16px;
  }}
  .modal input[type="text"] {{
    flex: 1; padding: 8px 12px; background: #0d1117; border: 1px solid #30363d;
    border-radius: 6px; color: #e6edf3; font-size: 0.85rem; outline: none;
  }}
  .modal input[type="text"]:focus {{ border-color: #58a6ff; }}
  .modal .search-btn {{
    padding: 8px 14px; background: #238636; border: none; border-radius: 6px;
    color: #fff; cursor: pointer; font-size: 0.82rem;
  }}
  .modal .search-btn:hover {{ background: #2ea043; }}
  .modal .close-btn {{
    position: absolute; top: 12px; right: 16px; background: none; border: none;
    color: #8b949e; font-size: 1.2rem; cursor: pointer;
  }}
  .acl-list {{ list-style: none; margin-bottom: 12px; }}
  .acl-item {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 10px; border-bottom: 1px solid #21262d; font-size: 0.85rem;
  }}
  .acl-item .user-info {{ color: #e6edf3; }}
  .acl-item .user-info .team {{ color: #8b949e; font-size: 0.75rem; }}

  /* Tab system */
  .hub-tabs {{ display: flex; gap: 0; margin-top: 24px; border-bottom: 1px solid #30363d; }}
  .hub-tab {{
    padding: 10px 20px; font-size: 0.88rem; color: #8b949e; cursor: pointer;
    border-bottom: 2px solid transparent; transition: all 0.2s;
    background: none; border-top: none; border-left: none; border-right: none;
  }}
  .hub-tab:hover {{ color: #e6edf3; }}
  .hub-tab.active {{ color: #58a6ff; border-bottom-color: #58a6ff; }}
  .hub-tab-content {{ display: none; }}
  .hub-tab-content.active {{ display: block; }}

  /* Announcement banner */
  .announce-banner {{
    background: #1a365d;
    border: 1px solid #2563eb;
    border-radius: 6px;
    padding: 10px 16px;
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 14px;
    color: #93c5fd;
  }}
  .announce-detail-btn {{
    background: none;
    border: 1px solid #2563eb;
    color: #60a5fa;
    padding: 4px 10px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
    margin-left: auto;
  }}
  .announce-dismiss {{
    background: none;
    border: none;
    color: #666;
    font-size: 18px;
    cursor: pointer;
    padding: 0 4px;
  }}

  .footer {{ text-align: center; margin-top: 32px; color: #484f58; font-size: 0.75rem; }}

  .btn-stop-all {{ padding:6px 14px; background:#da3633; border:none; border-radius:6px; color:#fff; font-size:0.78rem; cursor:pointer; margin-top:8px; }}
  .resource-warning {{ margin-top:8px; padding:8px 12px; background:#3d1f1f; border:1px solid #da3633; border-radius:8px; font-size:0.75rem; color:#f85149; line-height:1.4; }}
  .badge-yellow {{ background: #3d2e00; color: #d29922; }}
  .status-badge {{ display:inline-block; padding:2px 8px; border-radius:10px; font-size:0.7rem; font-weight:600; }}
  .status-deployed {{ background:#1a3a2a; color:#3fb950; }}
  .status-suspended {{ background:#3d2e00; color:#d29922; }}
  .status-running {{ background:#1f3a5f; color:#58a6ff; }}
  .status-stopped {{ background:#21262d; color:#8b949e; }}
  .status-deleted {{ background:#3d1f1f; color:#f85149; opacity:0.6; }}
  /* Scrollbar */
  .app-list.scrollable {{ max-height: 400px; overflow-y: auto; }}
  .app-list.scrollable::-webkit-scrollbar {{ width: 6px; }}
  .app-list.scrollable::-webkit-scrollbar-track {{ background: #30363d; border-radius: 3px; }}
  .app-list.scrollable::-webkit-scrollbar-thumb {{ background: #484f58; border-radius: 3px; }}
  .app-list.scrollable::-webkit-scrollbar-thumb:hover {{ background: #6e7681; }}

  /* Metric badges */
  .metric-badge {{ display:inline-block; padding:2px 8px; border-radius:10px; font-size:0.68rem;
    background:#21262d; color:#8b949e; }}
  .metric-badge span {{ font-weight:600; color:#58a6ff; }}

  /* Stats modal */
  .stat-card {{ flex:1; background:#0d1117; border:1px solid #30363d; border-radius:8px; padding:12px; text-align:center; }}
  .stat-value {{ font-size:1.4rem; font-weight:700; color:#58a6ff; }}
  .stat-label {{ font-size:0.72rem; color:#8b949e; margin-top:4px; }}
  .dau-chart {{ display:flex; align-items:flex-end; gap:2px; height:120px; padding:8px 0; overflow-x:auto; }}
  .bar-group {{ display:flex; flex-direction:column; align-items:center; flex:1; min-width:12px; }}
  .bar {{ background:#58a6ff; border-radius:2px 2px 0 0; min-height:2px; width:100%; position:relative; transition:height 0.3s; }}
  .bar:hover {{ background:#79c0ff; }}
  .bar-tooltip {{ display:none; position:absolute; top:-24px; left:50%; transform:translateX(-50%);
    background:#21262d; color:#e6edf3; padding:2px 6px; border-radius:4px; font-size:0.68rem; white-space:nowrap; z-index:10; }}
  .bar:hover .bar-tooltip {{ display:block; }}
  .bar-label {{ font-size:0.55rem; color:#484f58; margin-top:2px; writing-mode:vertical-rl; }}
  .visitors-table {{ width:100%; border-collapse:collapse; font-size:0.78rem; }}
  .visitors-table th {{ text-align:left; color:#8b949e; font-weight:500; padding:6px 8px; border-bottom:1px solid #30363d; }}
  .visitors-table td {{ padding:6px 8px; color:#e6edf3; border-bottom:1px solid #21262d; }}

  /* MMS modal textarea */
  .mms-textarea {{ width:100%; padding:10px 12px; background:#0d1117; border:1px solid #30363d;
    border-radius:6px; color:#e6edf3; font-size:0.85rem; resize:vertical; outline:none; font-family:inherit; }}
  .mms-textarea:focus {{ border-color:#58a6ff; }}
  /* Share management */
  .share-check {{ margin-right:10px; accent-color:#58a6ff; }}
  .share-tag {{ display:inline-block; padding:1px 6px; border-radius:4px; font-size:0.68rem; font-weight:600; margin-right:6px; }}
  .share-tag-app {{ background:#1f3a5f; color:#58a6ff; }}
  .share-tag-data {{ background:#1a3a2a; color:#3fb950; }}
  /* Skill store */
  .skill-item {{ display:flex; align-items:center; justify-content:space-between; padding:12px; border:1px solid #21262d; border-radius:8px; margin-bottom:8px; }}
  .skill-meta {{ font-size:0.75rem; color:#8b949e; margin-top:2px; }}
  .skill-rank {{ font-size:1.2rem; margin-right:12px; min-width:28px; text-align:center; }}
  .store-header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; }}
  .store-search {{ padding:6px 10px; background:#0d1117; border:1px solid #30363d; border-radius:6px; color:#e6edf3; font-size:0.82rem; outline:none; width:200px; }}

  /* ===== File Explorer (Windows 11 Dark) ===== */
  .file-explorer-container {{
    background: #1e1e2e; border-radius: 12px; border: 1px solid #333348;
    overflow: hidden; margin-top: 16px;
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
  }}
  .file-toolbar {{
    display: flex; align-items: center; gap: 8px; padding: 8px 12px;
    background: #252536; border-bottom: 1px solid #333348; flex-wrap: wrap;
  }}
  .file-toolbar .breadcrumb {{
    display: flex; align-items: center; gap: 2px; flex: 1; min-width: 200px;
    background: #1a1a2e; border-radius: 4px; padding: 6px 10px;
    overflow-x: auto; white-space: nowrap; font-size: 13px; color: #cdd6f4;
    border: 1px solid #333348;
  }}
  .file-toolbar .breadcrumb .bc-sep {{ color: #585b70; margin: 0 2px; font-size: 11px; user-select: none; }}
  .file-toolbar .breadcrumb .bc-item {{
    cursor: pointer; padding: 2px 4px; border-radius: 3px; color: #89b4fa; transition: background 0.15s;
  }}
  .file-toolbar .breadcrumb .bc-item:hover {{ background: #2a2d3e; text-decoration: underline; }}
  .file-toolbar .breadcrumb .bc-item.current {{ color: #cdd6f4; cursor: default; }}
  .file-toolbar .breadcrumb .bc-item.current:hover {{ background: transparent; text-decoration: none; }}
  .file-toolbar .breadcrumb .bc-home {{ cursor: pointer; font-size: 15px; padding: 2px 4px; border-radius: 3px; transition: background 0.15s; }}
  .file-toolbar .breadcrumb .bc-home:hover {{ background: #2a2d3e; }}
  .fe-btn {{
    display: inline-flex; align-items: center; gap: 5px; padding: 6px 14px;
    border: 1px solid #444466; background: #2a2d3e; color: #cdd6f4; border-radius: 4px;
    cursor: pointer; font-size: 12.5px; font-family: inherit; transition: background 0.15s, border-color 0.15s; white-space: nowrap;
  }}
  .fe-btn:hover {{ background: #363952; border-color: #5865f2; }}
  .fe-btn.danger {{ border-color: #f3425f; color: #f3425f; }}
  .fe-btn.danger:hover {{ background: #3d1a24; }}
  .fe-btn.primary {{ background: #264f78; border-color: #3a7bd5; color: #fff; }}
  .fe-btn.primary:hover {{ background: #2d5f8e; }}

  /* Tabulator overrides */
  #file-table .tabulator-header {{
    background: #252536 !important; border-bottom: 1px solid #333348 !important;
    color: #a6adc8 !important; font-weight: 600; font-size: 12px;
  }}
  #file-table .tabulator-header .tabulator-col {{
    background: #252536 !important; border-right: 1px solid #333348 !important;
  }}
  #file-table .tabulator-header .tabulator-col:hover {{ background: #2a2d3e !important; }}
  #file-table .tabulator-header .tabulator-col .tabulator-col-content {{ padding: 8px 10px; }}
  #file-table .tabulator-header .tabulator-col-resize-handle {{ width: 6px; right: -3px; }}
  #file-table .tabulator-header .tabulator-col-resize-handle:hover {{ background: #5865f2; opacity: 0.5; }}
  #file-table .tabulator-tableholder {{ background: #1e1e2e; }}
  #file-table .tabulator-row {{
    background: #1e1e2e !important; border-bottom: 1px solid #292940 !important;
    color: #cdd6f4; transition: background 0.1s; min-height: 36px;
  }}
  #file-table .tabulator-row:hover {{ background: #2a2d3e !important; }}
  #file-table .tabulator-row.tabulator-selected {{ background: #264f78 !important; }}
  #file-table .tabulator-row.tabulator-selected:hover {{ background: #2d5f8e !important; }}
  #file-table .tabulator-row .tabulator-cell {{ border-right: none !important; padding: 6px 10px; }}
  #file-table .tabulator-row .tabulator-cell.tabulator-frozen {{ background: inherit !important; }}
  #file-table .tabulator-row .tabulator-cell input[type="checkbox"],
  #file-table .tabulator-header .tabulator-col input[type="checkbox"] {{
    accent-color: #5865f2; width: 15px; height: 15px; cursor: pointer;
  }}
  .fe-name-cell {{ display: flex; align-items: center; gap: 8px; cursor: default; user-select: none; }}
  .fe-name-cell .fe-icon {{ font-size: 16px; flex-shrink: 0; width: 20px; text-align: center; }}
  .fe-name-cell.is-dir {{ cursor: pointer; }}
  .fe-name-cell.is-dir .fe-label {{ color: #89b4fa; }}
  .fe-name-cell.is-dir:hover .fe-label {{ text-decoration: underline; }}
  .fe-name-cell .fe-label {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .fe-empty {{ text-align: center; padding: 48px 20px; color: #585b70; font-size: 14px; }}
  .fe-empty .fe-empty-icon {{ font-size: 40px; margin-bottom: 12px; opacity: 0.5; }}

  /* Context menu */
  .fe-context-menu {{
    position: fixed; background: #252536; border: 1px solid #444466; border-radius: 6px;
    padding: 4px 0; min-width: 180px; z-index: 10000; box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    font-size: 13px; display: none;
  }}
  .fe-context-menu.visible {{ display: block; }}
  .fe-context-menu .ctx-item {{
    display: flex; align-items: center; gap: 10px; padding: 7px 16px;
    cursor: pointer; color: #cdd6f4; transition: background 0.1s;
  }}
  .fe-context-menu .ctx-item:hover {{ background: #2a2d3e; }}
  .fe-context-menu .ctx-item.danger {{ color: #f3425f; }}
  .fe-context-menu .ctx-item.danger:hover {{ background: #3d1a24; }}
  .fe-context-menu .ctx-sep {{ height: 1px; background: #333348; margin: 4px 0; }}
  .fe-context-menu .ctx-icon {{ width: 18px; text-align: center; font-size: 14px; }}
  #fe-upload-input {{ display: none; }}
</style>
</head>
<body>

<div class="container">
  <div class="header">
    <h1>Otto AI <span class="accent">터미널</span></h1>
    <p>{user_name} ({user_id}) &middot; {pod_name}</p>
    <a href="/" class="logout-btn" style="border-color:#da3633;color:#da3633;" id="logoutBtn">로그아웃 &amp; 종료</a>
  </div>
  <script>
  document.getElementById('logoutBtn').addEventListener('click', function(e) {{
    e.preventDefault();
    if (!confirm('로그아웃 및 Pod을 종료합니다.\\n대화 내용은 자동 백업되어 다음 로그인 시 복원됩니다.')) return;
    var cookies = document.cookie.split(';');
    var token = '';
    for (var i = 0; i < cookies.length; i++) {{
      var c = cookies[i].trim();
      if (c.indexOf('claude_token=') === 0) {{
        token = c.substring('claude_token='.length);
        break;
      }}
    }}
    var cleanup = function() {{
      localStorage.clear();
      document.cookie = 'claude_token=;path=/;max-age=0';
      window.location.href = '/';
    }};
    var headers = {{}};
    if (token) {{
      headers['Authorization'] = 'Bearer ' + token;
    }}
    fetch('/api/v1/sessions/', {{
      method: 'DELETE',
      headers: headers
    }}).then(cleanup).catch(cleanup);
  }});
  </script>

  <!-- 공지 배너 -->
  <div id="announceBanner" style="display:none" class="announce-banner">
    <span id="announceBannerIcon">&#128226;</span>
    <span id="announceBannerText"></span>
    <button onclick="showAnnouncementModal()" class="announce-detail-btn">자세히</button>
    <button onclick="dismissAnnouncement()" class="announce-dismiss">&times;</button>
  </div>

  <!-- 뒤로가기 버튼 -->
  <div id="hubBackBtn" style="display:none;margin-bottom:16px">
    <button onclick="backToHub()" style="background:none;border:1px solid #444;color:#ccc;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:14px">&#8592; 허브로 돌아가기</button>
  </div>

  <div class="cards">
    <a class="card" href="/terminal/{pod_name}/" id="terminalCard" onclick="return openTerminal(event, this)">
      <div class="icon" id="terminalIcon">&#9000;</div>
      <h2 id="terminalTitle">터미널 접속</h2>
      <p id="terminalDesc">Claude Code AI 코딩 어시스턴트<br>웹 터미널에서 바로 실행</p>
      <span class="badge badge-blue" id="terminalBadge">탭에서 열기</span>
    </a>
    <div class="card" style="cursor:pointer" onclick="openHubPage('files')">
      <div class="icon">&#128228;</div>
      <h2>파일 관리</h2>
      <p>파일 업로드·다운로드</p>
      <span class="badge badge-green">파일 탐색기</span>
    </div>
    <a class="card" href="/gallery/" target="_blank" style="text-decoration:none;color:inherit">
      <div class="icon">&#127981;</div>
      <h2>앱 갤러리</h2>
      <p>전사 공개 앱 둘러보기</p>
      <span class="badge badge-blue">새 탭에서 열기</span>
    </a>
    <div class="card" style="cursor:pointer" onclick="openHubPage('apps')">
      <div class="icon">&#128202;</div>
      <h2>내 앱 관리</h2>
      <p>배포 통계·접근 권한 설정</p>
      <span class="badge badge-green">앱 관리</span>
    </div>
    <div class="card" style="cursor:pointer" onclick="openHubPage('skills')">
      <div class="icon">&#128161;</div>
      <h2>스킬 공유</h2>
      <p>업무 노하우 공유·설치</p>
      <span class="badge badge-green">스킬 스토어</span>
    </div>
    <div class="card" style="cursor:pointer" onclick="openHubPage('guide')">
      <div class="icon">&#128214;</div>
      <h2>가이드</h2>
      <p>명령어·활용 가이드</p>
      <span class="badge badge-green">바로보기</span>
    </div>
  </div>

  <!-- 탭 1: 앱 관리 (내 앱 관리 페이지로 이동 - /portal/ 사용) -->
  <div class="hub-tab-content" id="tab-apps">

  <!-- 내 웹앱 (통합 뷰) -->
  <div class="app-section" id="myAppsSection">
    <h3>내 웹앱 <span class="count" id="myAppsCount">(0)</span></h3>
    <ul class="app-list scrollable" id="myAppsList">
      <li class="empty-msg">앱이 없습니다. 터미널에서 <code>deploy my-app</code>으로 배포하거나, 프로젝트를 생성하세요.</li>
    </ul>
  </div>

  <!-- 나에게 공유된 앱 -->
  <div class="app-section" id="sharedAppsSection">
    <h3>공유 받은 앱 <span class="count" id="sharedAppsCount">(0)</span></h3>
    <ul class="app-list" id="sharedAppsList">
      <li class="empty-msg">공유 받은 앱이 없습니다.</li>
    </ul>
  </div>

  <!-- 내 공유 관리 -->
  <div class="app-section" id="mySharesSection">
    <h3 style="display:flex;justify-content:space-between;align-items:center;">
      <span>내 공유 관리 <span class="count" id="mySharesCount">(0)</span></span>
      <button class="btn-sm danger" id="bulkRevokeBtn" style="display:none;padding:5px 12px;font-size:0.78rem;" onclick="bulkRevokeShares()">선택 항목 공유 해제</button>
    </h3>
    <div style="padding:4px 12px;margin-bottom:8px;">
      <label style="font-size:0.78rem;color:#8b949e;cursor:pointer;">
        <input type="checkbox" class="share-check" id="shareSelectAll" onchange="toggleShareSelectAll(this.checked)"> 전체 선택
      </label>
    </div>
    <ul class="app-list scrollable" id="mySharesList">
      <li class="empty-msg">공유 항목이 없습니다.</li>
    </ul>
  </div>

  </div><!-- /tab-apps -->

  <!-- 탭 2: 파일 관리 -->
  <div class="hub-tab-content" id="tab-files">
    <div class="file-explorer-container">
      <div class="file-toolbar">
        <button class="fe-btn" onclick="navigateUp()" title="상위 폴더"><span style="font-size:14px;">&#11014;</span></button>
        <div class="breadcrumb" id="fe-breadcrumb">
          <span class="bc-home" onclick="loadDirectory('')">&#127968;</span>
          <span class="bc-sep">&#8250;</span>
          <span class="bc-item current">workspace</span>
        </div>
        <button class="fe-btn primary" onclick="document.getElementById('fe-upload-input').click()">&#128228; 업로드</button>
        <button class="fe-btn" onclick="createNewFolder()">&#128193; 새 폴더</button>
        <button class="fe-btn danger" onclick="deleteSelectedFiles()">&#128465; 삭제</button>
      </div>
      <div id="file-table"></div>
      <input type="file" id="fe-upload-input" multiple onchange="uploadFilesExplorer(this.files)" />
    </div>

    <!-- 내 공유 데이터 -->
    <div class="app-section" id="myDatasetsSection">
      <h3 style="display:flex;justify-content:space-between;align-items:center;">
        <span>내 공유 데이터 <span class="count" id="myDatasetsCount">(0)</span></span>
        <button class="btn-sm" style="border-color:#238636;color:#3fb950;padding:5px 12px;font-size:0.78rem;"
                onclick="toggleRegisterForm()">+ 데이터셋 등록</button>
      </h3>
      <div id="registerDatasetForm" style="display:none;margin-bottom:14px;padding:14px;background:#0d1117;border:1px solid #30363d;border-radius:8px;">
        <div style="font-size:0.82rem;color:#8b949e;margin-bottom:10px;">
          workspace 내 파일/디렉토리를 공유 데이터셋으로 등록합니다.
        </div>
        <div style="display:flex;gap:8px;margin-bottom:8px;">
          <input type="text" id="regDatasetName" placeholder="데이터셋 이름 (예: erp-2026q1)"
                 style="flex:1;padding:7px 10px;background:#161b22;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:0.82rem;outline:none;">
        </div>
        <div style="display:flex;gap:8px;margin-bottom:8px;">
          <input type="text" id="regFilePath" placeholder="[찾아보기]로 파일 또는 폴더 선택"
                 style="flex:1;padding:7px 10px;background:#161b22;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:0.82rem;outline:none;" readonly>
          <input type="hidden" id="regFileType" value="directory">
          <button onclick="openFileBrowser(function(path) {{ document.getElementById('regFilePath').value = path; var ext = path.split('.').pop().toLowerCase(); var typeMap = {{'sqlite':'sqlite','xlsx':'excel','xls':'excel','csv':'csv'}}; document.getElementById('regFileType').value = typeMap[ext] || (path.endsWith('/') ? 'directory' : 'file'); }})"
                  style="padding:7px 14px;background:#21262d;border:1px solid #30363d;border-radius:6px;color:#58a6ff;font-size:0.82rem;cursor:pointer;white-space:nowrap;">찾아보기</button>
        </div>
        <div style="display:flex;gap:8px;">
          <input type="text" id="regDescription" placeholder="설명 (선택)"
                 style="flex:1;padding:7px 10px;background:#161b22;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:0.82rem;outline:none;">
          <button onclick="registerDataset()" style="padding:7px 16px;background:#238636;border:none;border-radius:6px;color:#fff;font-size:0.82rem;cursor:pointer;">등록</button>
        </div>
      </div>
      <ul class="app-list" id="myDatasetsList">
        <li class="empty-msg">공유 데이터가 없습니다. 위 "데이터셋 등록" 버튼으로 등록하거나, 10MB 초과 파일 업로드 시 자동 생성됩니다.</li>
      </ul>
    </div>

    <!-- 공유 받은 데이터 -->
    <div class="app-section" id="sharedDatasetsSection">
      <h3>공유 받은 데이터 <span class="count" id="sharedDatasetsCount">(0)</span></h3>
      <ul class="app-list" id="sharedDatasetsList">
        <li class="empty-msg">공유 받은 데이터가 없습니다.</li>
      </ul>
    </div>
  </div><!-- /tab-files -->

  <!-- 탭 2: 스킬 관리 -->
  <div class="hub-tab-content" id="tab-skills">

  <!-- 내 스킬 -->
  <div class="app-section">
    <h3>내 스킬 <span class="count" id="mySkillsCount">(0)</span></h3>
    <ul class="app-list" id="mySkillsList">
      <li class="empty-msg">등록된 스킬이 없습니다.</li>
    </ul>
  </div>

  <!-- 스킬 스토어 -->
  <div class="app-section">
    <h3>스킬 스토어</h3>
    <div class="store-header">
      <div>
        <button class="btn-sm" style="border-color:#58a6ff;color:#58a6ff;" onclick="loadSkillStore('popular')">인기순</button>
        <button class="btn-sm" style="margin-left:4px;" onclick="loadSkillStore('recent')">최신순</button>
      </div>
      <input type="text" class="store-search" id="skillStoreSearch" placeholder="스킬 검색..." onkeypress="if(event.key==='Enter')loadSkillStore('search')">
    </div>
    <ul class="app-list scrollable" id="skillStoreList">
      <li class="empty-msg">스킬 스토어를 불러오는 중...</li>
    </ul>
  </div>

  <!-- 설치된 스킬 -->
  <div class="app-section">
    <h3>설치된 스킬 <span class="count" id="installedSkillsCount">(0)</span></h3>
    <ul class="app-list" id="installedSkillsList">
      <li class="empty-msg">설치된 스킬이 없습니다.</li>
    </ul>
  </div>

  </div><!-- /tab-skills -->

  <!-- 탭 3: 명령어 가이드 -->
  <div class="hub-tab-content" id="tab-guide">

  <div class="guide">
    <h3>활용 가이드 <span class="count" id="guidesCount">(0)</span></h3>
    <div id="guidesList" style="margin-bottom:16px">
      <p style="color:#888;font-size:13px">가이드를 불러오는 중...</p>
    </div>
  </div>

  <div class="guide" style="margin-top:16px">
    <h3>슬래시 명령어 (Claude 대화 중 입력)</h3>
    <div class="guide-grid">
      <div class="guide-item"><code>/db</code> <span class="label">DB 조회 (TANGO/Safety/SQLite)</span></div>
      <div class="guide-item"><code>/report</code> <span class="label">보고서 생성</span></div>
      <div class="guide-item"><code>/excel</code> <span class="label">엑셀 파일 생성</span></div>
      <div class="guide-item"><code>/share</code> <span class="label">파일/데이터 공유 관리</span></div>
      <div class="guide-item"><code>/webapp</code> <span class="label">웹앱 개발 가이드</span></div>
      <div class="guide-item"><code>/sms</code> <span class="label">SMS 발송</span></div>
    </div>
  </div>

  <div class="guide" style="margin-top:12px;">
    <h3>@ 멘션 (파일/컨텍스트 참조)</h3>
    <div class="guide-grid">
      <div class="guide-item"><code>@파일명</code> <span class="label">파일 내용을 대화에 포함</span></div>
      <div class="guide-item"><code>@workspace</code> <span class="label">프로젝트 전체 구조 참조</span></div>
    </div>
  </div>

  <div class="guide" style="margin-top:12px;">
    <h3>Superpowers 스킬 (고급 기능)</h3>
    <div class="guide-grid">
      <div class="guide-item"><code>/brainstorm</code> <span class="label">아이디어 탐색 + 설계</span></div>
      <div class="guide-item"><code>/debug</code> <span class="label">체계적 디버깅</span></div>
      <div class="guide-item"><code>/plan</code> <span class="label">구현 계획 수립</span></div>
      <div class="guide-item"><code>/tdd</code> <span class="label">테스트 주도 개발</span></div>
      <div class="guide-item"><code>/review</code> <span class="label">코드 리뷰 요청</span></div>
      <div class="guide-item"><code>/git</code> <span class="label">Git 워크트리 관리</span></div>
    </div>
  </div>

  <div class="guide" style="margin-top:12px;">
    <h3>Serena (코드 시맨틱 분석)</h3>
    <div class="guide-grid">
      <div class="guide-item"><code>/sc:load</code> <span class="label">프로젝트 컨텍스트 로딩</span></div>
      <div class="guide-item"><code>/sc:save</code> <span class="label">세션 저장</span></div>
      <div class="guide-item"><code>/sc:analyze</code> <span class="label">코드 분석 (품질/보안/성능)</span></div>
      <div class="guide-item"><code>/sc:explain</code> <span class="label">코드 설명</span></div>
    </div>
  </div>

  <div class="guide" style="margin-top:12px;">
    <h3>터미널 명령어</h3>
    <div class="guide-grid">
      <div class="guide-item"><code>deploy my-app</code> <span class="label">웹앱 배포 (팀 공유)</span></div>
      <div class="guide-item"><code>undeploy my-app</code> <span class="label">웹앱 삭제</span></div>
      <div class="guide-item"><code>backup-chat</code> <span class="label">대화이력 수동 백업</span></div>
      <div class="guide-item"><code>psql-tango</code> <span class="label">TANGO 알람 DB</span></div>
    </div>
  </div>

  </div><!-- /tab-guide -->

  <div class="footer">Claude Code Platform &middot; Powered by AWS Bedrock</div>

  <!-- 공지사항 모달 -->
  <div id="announceModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:1000">
    <div style="max-width:600px;margin:80px auto;background:#1e1e2e;border:1px solid #333;border-radius:8px;padding:24px;max-height:80vh;overflow-y:auto">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h2 style="margin:0;font-size:18px;color:#fff" id="announceModalTitle">공지사항</h2>
        <button onclick="closeAnnouncementModal()" style="background:none;border:none;color:#888;font-size:20px;cursor:pointer">&times;</button>
      </div>
      <div id="announceModalBody" style="color:#ccc;line-height:1.6"></div>
      <div id="announceModalDate" style="color:#666;font-size:12px;margin-top:16px"></div>
    </div>
  </div>

  <!-- 파일 브라우저 모달 -->
  <div class="modal-overlay" id="fileBrowserModal">
    <div class="modal" style="position:relative;width:500px;">
      <button class="close-btn" onclick="closeFileBrowser()">&times;</button>
      <h3>파일 선택</h3>
      <div id="fileBreadcrumb" style="font-size:0.78rem;color:#58a6ff;margin-bottom:12px;cursor:pointer;"></div>
      <div id="fileBrowserList" style="max-height:400px;overflow-y:auto;"></div>
      <div id="fileSelected" style="margin-top:12px;padding:8px 10px;background:#0d1117;border:1px solid #30363d;border-radius:6px;font-size:0.82rem;color:#8b949e;display:none;">
        <span>선택: </span><strong id="fileSelectedPath" style="color:#e6edf3;"></strong>
        <button onclick="confirmFileSelection()" style="float:right;padding:4px 14px;background:#238636;border:none;border-radius:4px;color:#fff;font-size:0.78rem;cursor:pointer;">확인</button>
      </div>
    </div>
  </div>

  <!-- ACL 관리 모달 -->
  <div class="modal-overlay" id="aclModal">
    <div class="modal" style="position:relative;width:460px;">
      <button class="close-btn" onclick="closeAclModal()">&times;</button>
      <h3>접근 권한 관리 — <span id="aclAppName"></span></h3>
      <!-- Grant type selector -->
      <div style="margin-bottom:14px;">
        <label style="font-size:0.78rem;color:#8b949e;display:block;margin-bottom:6px;">접근 유형</label>
        <select id="aclGrantType" onchange="onAclGrantTypeChange()" style="width:100%;padding:8px 12px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:0.85rem;">
          <option value="user">사용자 (개인)</option>
          <option value="team">팀</option>
          <option value="region">지역</option>
          <option value="job">직책</option>
          <option value="company">전사 공개</option>
        </select>
      </div>
      <!-- Panel: user search (default) -->
      <div id="aclUserPanel">
        <div class="search-box">
          <input type="text" id="aclSearchInput" placeholder="사번 또는 이름 검색..." onkeypress="if(event.key==='Enter')searchUsers()">
          <button class="search-btn" onclick="searchUsers()">검색</button>
        </div>
        <div id="searchResults"></div>
      </div>
      <!-- Panel: dropdown select (team/region/job) -->
      <div id="aclSelectPanel" style="display:none;margin-bottom:12px;">
        <div style="display:flex;gap:8px;">
          <select id="aclGrantValueSelect" style="flex:1;padding:8px 12px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:0.85rem;">
            <option value="">선택하세요</option>
          </select>
          <button class="search-btn" onclick="grantAccessByType()" style="white-space:nowrap;">추가</button>
        </div>
      </div>
      <h4 style="font-size:0.85rem;color:#8b949e;margin:12px 0 8px;">현재 접근 권한</h4>
      <ul class="acl-list" id="aclUserList"></ul>
    </div>
  </div>

  <!-- 통계 모달 -->
  <div id="statsModal" class="modal-overlay">
    <div class="modal" style="width:560px;max-width:90vw;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
        <h3><span id="statsAppName"></span> 통계</h3>
        <button class="btn-sm" onclick="closeStatsModal()">닫기</button>
      </div>
      <div style="display:flex;gap:16px;margin-bottom:20px;">
        <div class="stat-card"><div class="stat-value" id="statDau">-</div><div class="stat-label">오늘 DAU</div></div>
        <div class="stat-card"><div class="stat-value" id="statMau">-</div><div class="stat-label">이번 달 MAU</div></div>
        <div class="stat-card"><div class="stat-value" id="statTotal">-</div><div class="stat-label">총 조회수</div></div>
      </div>
      <div style="margin-bottom:16px;">
        <h4 style="font-size:0.85rem;color:#8b949e;margin-bottom:10px;">30일 DAU</h4>
        <div id="dauChart" class="dau-chart"></div>
      </div>
      <div>
        <h4 style="font-size:0.85rem;color:#8b949e;margin-bottom:10px;">최근 방문자</h4>
        <table class="visitors-table">
          <thead><tr><th>사번</th><th>이름</th><th>부서</th><th>방문일시</th></tr></thead>
          <tbody id="visitorsBody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- MMS 공유 모달 -->
  <div id="mmsModal" class="modal-overlay">
    <div class="modal" style="width:460px;">
      <h3 style="margin-bottom:16px;"><span id="mmsAppName"></span> MMS 공유</h3>
      <p style="font-size:0.78rem;color:#8b949e;margin-bottom:12px;">ACL에 등록된 사용자에게 앱 공유 MMS를 발송합니다.</p>
      <textarea id="mmsMessage" class="mms-textarea" rows="4" maxlength="500" placeholder="공유 메시지를 입력하세요..."
        oninput="document.getElementById('mmsCharCount').textContent=this.value.length"></textarea>
      <div style="display:flex;justify-content:space-between;font-size:0.72rem;color:#8b949e;margin:6px 0 12px;">
        <span><span id="mmsCharCount">0</span>/500자</span>
      </div>
      <div id="mmsError" style="display:none;color:#da3633;font-size:0.82rem;margin-bottom:12px;padding:8px;background:#3d1114;border-radius:6px;"></div>
      <div id="mmsSuccess" style="display:none;color:#3fb950;font-size:0.82rem;margin-bottom:12px;padding:8px;background:#0d2818;border-radius:6px;"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;">
        <button class="btn-sm" onclick="closeMmsModal()">취소</button>
        <button class="btn-sm" id="mmsSendBtn" style="border-color:#238636;color:#3fb950;" onclick="sendMmsFromHub()">발송</button>
      </div>
    </div>
  </div>

  <!-- 웹앱 공유(배포) 모달 -->
  <div class="modal-overlay" id="deployModal">
    <div class="modal-content">
      <div class="modal-header">
        <h3>웹앱 공유하기</h3>
        <button class="close-btn" onclick="closeDeployModal()">&times;</button>
      </div>
      <div style="padding:16px;">
        <div style="margin-bottom:16px;">
          <div style="font-size:0.82rem;color:#8b949e;margin-bottom:4px;">앱 이름</div>
          <div id="deployAppName" style="font-weight:bold;font-size:1rem;"></div>
        </div>
        <div style="margin-bottom:16px;">
          <div style="font-size:0.82rem;color:#8b949e;margin-bottom:8px;">공개 범위</div>
          <label style="display:block;margin-bottom:6px;cursor:pointer;">
            <input type="radio" name="deployVisibility" value="private" checked onchange="document.getElementById('deployAclSection').style.display='block'"> 비공개 (허용된 사용자만)
          </label>
          <label style="display:block;cursor:pointer;">
            <input type="radio" name="deployVisibility" value="company" onchange="document.getElementById('deployAclSection').style.display='none'"> 전사 공개 (모든 임직원)
          </label>
        </div>
        <div id="deployAclSection" style="margin-bottom:16px;">
          <div style="font-size:0.82rem;color:#8b949e;margin-bottom:8px;">접근 허용 사용자</div>
          <div style="display:flex;gap:8px;margin-bottom:8px;">
            <input type="text" id="deployUserSearch" placeholder="사번 또는 이름 검색..."
                   style="flex:1;padding:7px 10px;background:#161b22;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:0.82rem;outline:none;"
                   onkeypress="if(event.key==='Enter')searchDeployUsers()">
            <button onclick="searchDeployUsers()" style="padding:7px 14px;background:#21262d;border:1px solid #30363d;border-radius:6px;color:#58a6ff;font-size:0.82rem;cursor:pointer;">검색</button>
          </div>
          <ul class="acl-list" id="deploySearchResults" style="max-height:120px;overflow-y:auto;"></ul>
          <div style="font-size:0.78rem;color:#8b949e;margin-top:8px;">선택된 사용자:</div>
          <ul class="acl-list" id="deploySelectedUsers" style="max-height:100px;overflow-y:auto;"></ul>
        </div>
        <button onclick="executeDeploy()" style="width:100%;padding:10px;background:#238636;border:none;border-radius:8px;color:#fff;font-size:0.9rem;font-weight:bold;cursor:pointer;">배포하기</button>
      </div>
    </div>
  </div>

  <!-- 데이터 공유 관리 모달 -->
  <div class="modal-overlay" id="dataShareModal">
    <div class="modal" style="position:relative;width:480px;">
      <button class="close-btn" onclick="closeDataShareModal()">&times;</button>
      <h3>공유 관리 — <span id="dataShareName"></span></h3>
      <div style="display:flex;gap:0;margin-bottom:16px;border-bottom:1px solid #30363d;">
        <button class="share-tab active" id="shareTabUser" onclick="switchShareTab('user')"
          style="flex:1;padding:8px;background:none;border:none;border-bottom:2px solid #58a6ff;color:#e6edf3;cursor:pointer;font-size:0.85rem;">개인</button>
        <button class="share-tab" id="shareTabRegion" onclick="switchShareTab('region')"
          style="flex:1;padding:8px;background:none;border:none;border-bottom:2px solid transparent;color:#8b949e;cursor:pointer;font-size:0.85rem;">담당</button>
        <button class="share-tab" id="shareTabTeam" onclick="switchShareTab('team')"
          style="flex:1;padding:8px;background:none;border:none;border-bottom:2px solid transparent;color:#8b949e;cursor:pointer;font-size:0.85rem;">팀</button>
      </div>
      <!-- 개인 검색 -->
      <div id="shareUserPanel">
        <div style="display:flex;gap:8px;margin-bottom:8px;">
          <input type="text" id="shareUserSearch" placeholder="사번 또는 이름 검색..."
            style="flex:1;padding:8px 12px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:0.85rem;outline:none;"
            onkeypress="if(event.key==='Enter')searchShareUsers()">
          <select id="shareJobFilter" style="padding:8px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:0.82rem;">
            <option value="">전체 직책</option>
            <option value="실장">실장</option>
            <option value="담당">담당</option>
            <option value="팀장">팀장</option>
          </select>
          <button class="search-btn" onclick="searchShareUsers()">검색</button>
        </div>
        <div id="shareUserResults"></div>
      </div>
      <!-- 담당 선택 -->
      <div id="shareRegionPanel" style="display:none;">
        <div style="display:flex;gap:8px;">
          <select id="shareRegionSelect" style="flex:1;padding:8px 12px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:0.85rem;">
            <option value="">담당 선택...</option>
          </select>
          <button class="search-btn" onclick="shareToOrg('region')">전체 공유</button>
        </div>
        <div id="regionMembers" style="margin-top:10px;"></div>
      </div>
      <!-- 팀 선택 -->
      <div id="shareTeamPanel" style="display:none;">
        <div style="display:flex;gap:8px;">
          <select id="shareTeamSelect" style="flex:1;padding:8px 12px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:0.85rem;">
            <option value="">팀 선택...</option>
          </select>
          <button class="search-btn" onclick="shareToOrg('team')">전체 공유</button>
        </div>
        <div id="teamMembers" style="margin-top:10px;"></div>
      </div>
      <h4 style="font-size:0.85rem;color:#8b949e;margin:14px 0 8px;">현재 공유 대상</h4>
      <ul class="acl-list" id="dataShareList"></ul>
      <div style="margin-top:16px;padding-top:14px;border-top:1px solid #30363d;display:flex;justify-content:flex-end;">
        <button onclick="closeDataShareModal()"
          style="padding:8px 24px;background:#238636;border:none;border-radius:6px;color:#fff;font-size:0.85rem;cursor:pointer;">
          닫기
        </button>
      </div>
    </div>
  </div>
</div>

<!-- 파일 탐색기 우클릭 메뉴 -->
<div class="fe-context-menu" id="fe-context-menu">
  <div class="ctx-item" id="ctxPreviewItem" onclick="ctxPreview()"><span class="ctx-icon">&#128065;</span><span id="ctxPreviewLabel">&nbsp;미리보기</span></div>
  <div class="ctx-item" id="ctxEditItem" onclick="ctxEdit()"><span class="ctx-icon">&#128221;</span> 편집</div>
  <div class="ctx-item" id="ctxCoEditItem" onclick="ctxCoEdit()"><span class="ctx-icon">&#128101;</span> 함께 편집</div>
  <div class="ctx-item" onclick="ctxOpen()"><span class="ctx-icon">&#128194;</span> 열기</div>
  <div class="ctx-item" onclick="ctxDownload()"><span class="ctx-icon">&#128190;</span> 다운로드</div>
  <div class="ctx-sep"></div>
  <div class="ctx-item" onclick="ctxRename()"><span class="ctx-icon">&#9999;</span> 이름 바꾸기</div>
  <div class="ctx-item" onclick="ctxCopyPath()"><span class="ctx-icon">&#128203;</span> 경로 복사</div>
  <div class="ctx-sep"></div>
  <div class="ctx-item danger" onclick="ctxDelete()"><span class="ctx-icon">&#128465;</span> 삭제</div>
</div>

<div id="hubToast" style="position:fixed;top:20px;right:20px;padding:14px 22px;
  background:#1e293b;border:1px solid #58a6ff;border-radius:8px;color:#e6edf3;font-size:0.9rem;
  display:none;z-index:100;box-shadow:0 4px 20px rgba(0,0,0,0.4);">
</div>

<script>
var termWin = null;
var termCheckInterval = null;

function setCardState(active) {{
  var card = document.getElementById('terminalCard');
  var badge = document.getElementById('terminalBadge');
  var desc = document.getElementById('terminalDesc');
  if (active) {{
    card.style.borderColor = '#3fb950';
    card.style.opacity = '0.7';
    badge.textContent = '실행 중';
    badge.style.background = '#1a3a2a';
    badge.style.color = '#3fb950';
    desc.textContent = '터미널이 다른 탭에서 실행 중입니다.';
  }} else {{
    card.style.borderColor = '';
    card.style.opacity = '';
    badge.textContent = '탭에서 열기';
    badge.style.background = '';
    badge.style.color = '';
    desc.textContent = 'Claude Code AI 코딩 어시스턴트\\n웹 터미널에서 바로 실행';
  }}
}}

function startWatchingTerminal() {{
  if (termCheckInterval) clearInterval(termCheckInterval);
  termCheckInterval = setInterval(function() {{
    if (!termWin || termWin.closed) {{
      clearInterval(termCheckInterval);
      termWin = null;
      localStorage.removeItem('terminal_open');
      setCardState(false);
    }}
  }}, 1000);
}}

function openTerminal(e, el) {{
  e.preventDefault();
  if (termWin && !termWin.closed) {{
    termWin.focus();
    var t = document.getElementById('hubToast');
    t.textContent = '이미 열린 터미널 탭으로 이동합니다.';
    t.style.display = 'block';
    setTimeout(function() {{ t.style.display = 'none'; }}, 2000);
    return false;
  }}
  termWin = window.open(el.href, 'claude-terminal-session');
  if (termWin) {{
    localStorage.setItem('terminal_open', 'true');
    setCardState(true);
    startWatchingTerminal();
  }}
  return false;
}}

// ── 웹앱 관리 ──
var authHeaders = {{}};
(function() {{
  var cookies = document.cookie.split(';');
  for (var i = 0; i < cookies.length; i++) {{
    var c = cookies[i].trim();
    if (c.indexOf('claude_token=') === 0) {{
      authHeaders['Authorization'] = 'Bearer ' + c.substring('claude_token='.length);
      break;
    }}
  }}
}})();

function apiFetch(path, opts) {{
  opts = opts || {{}};
  opts.headers = Object.assign({{}}, authHeaders, opts.headers || {{}});
  return fetch('/api/v1' + path, opts).then(function(r) {{ return r.json(); }});
}}

var fileserverBase = '/files/{pod_name}';
function localFetch(path, opts) {{
  opts = opts || {{}};
  opts.headers = Object.assign({{}}, authHeaders, opts.headers || {{}});
  return fetch(fileserverBase + path, opts).then(function(r) {{ return r.json(); }});
}}

function esc(s) {{ var d = document.createElement('div'); d.textContent = s || ''; return d.textContent; }}

function buildAppItem(a, isOwner) {{
  var li = document.createElement('li'); li.className = 'app-item';
  var info = document.createElement('div'); info.className = 'app-info';
  var link = document.createElement('a'); link.className = 'app-name';
  link.href = a.app_url; link.target = '_blank'; link.textContent = a.app_name;
  var meta = document.createElement('div'); meta.className = 'app-meta';
  meta.textContent = isOwner ? (a.version + ' \u00b7 ' + a.status)
    : ((a.owner_name || a.owner_username) + ' \u00b7 ' + a.version);
  info.appendChild(link); info.appendChild(meta); li.appendChild(info);
  if (isOwner) {{
    var actions = document.createElement('div'); actions.className = 'app-actions';
    var aclBtn = document.createElement('button'); aclBtn.className = 'btn-sm';
    aclBtn.textContent = '\uc811\uadfc \uad00\ub9ac';
    aclBtn.onclick = function() {{ openAclModal(a.app_name); }};
    var delBtn = document.createElement('button'); delBtn.className = 'btn-sm danger';
    delBtn.textContent = '\uc0ad\uc81c';
    delBtn.onclick = function() {{ undeployApp(a.app_name); }};
    actions.appendChild(aclBtn); actions.appendChild(delBtn); li.appendChild(actions);
  }}
  return li;
}}

function loadMyApps() {{
  var platformApps = [];
  var localApps = [];
  var done = 0;

  function render() {{
    done++;
    if (done < 2) return;
    var section = document.getElementById('myAppsSection');
    var list = document.getElementById('myAppsList');
    var count = document.getElementById('myAppsCount');
    var deployedNames = {{}};
    platformApps.forEach(function(a) {{ deployedNames[a.app_name] = true; }});
    var localOnly = localApps.filter(function(a) {{ return !deployedNames[a.name]; }});
    var total = platformApps.length + localOnly.length;
    if (total === 0) {{ return; }}
    section.style.display = 'block';
    count.textContent = '(' + total + ')';
    list.replaceChildren();
    platformApps.forEach(function(a) {{
      list.appendChild(buildUnifiedAppItem({{
        name: a.app_name, app_name: a.app_name, status: a.status || 'deployed',
        app_url: a.app_url, version: a.version, visibility: a.visibility,
        dau: a.dau, mau: a.mau, acl_count: a.acl_count,
        is_platform: true
      }}));
    }});
    localOnly.forEach(function(a) {{
      var status = a.running ? 'running' : 'stopped';
      var appUrl = fileserverBase + '/webapp/' + a.port + '/';
      list.appendChild(buildUnifiedAppItem({{
        name: a.name, path: a.path, type: a.type, port: a.port,
        status: status, app_url: a.running ? appUrl : null,
        is_platform: false
      }}));
    }});
  }}

  apiFetch('/portal/my-apps').then(function(data) {{
    platformApps = data.apps || [];
    render();
  }}).catch(function() {{ render(); }});

  localFetch('/api/apps/status').then(function(data) {{
    localApps = data.apps || [];
    render();
  }}).catch(function() {{ render(); }});
}}

function loadSharedApps() {{
  apiFetch('/apps/shared').then(function(data) {{
    var apps = data.apps || [];
    var section = document.getElementById('sharedAppsSection');
    var list = document.getElementById('sharedAppsList');
    var count = document.getElementById('sharedAppsCount');
    if (apps.length === 0) {{ return; }}
    section.style.display = 'block';
    count.textContent = '(' + apps.length + ')';
    list.replaceChildren();
    apps.forEach(function(a) {{ list.appendChild(buildAppItem(a, false)); }});
  }}).catch(function() {{}});
}}

var aclOptions = null;
function loadAclOptions() {{
  if (aclOptions) return;
  apiFetch('/portal/acl-options').then(function(data) {{
    if (data && data.teams) aclOptions = data;
  }}).catch(function() {{}});
}}

var currentAclApp = '';
function openAclModal(appName) {{
  currentAclApp = appName;
  document.getElementById('aclAppName').textContent = appName;
  document.getElementById('aclGrantType').value = 'user';
  document.getElementById('aclUserPanel').style.display = '';
  document.getElementById('aclSelectPanel').style.display = 'none';
  document.getElementById('aclModal').classList.add('active');
  document.getElementById('aclSearchInput').value = '';
  document.getElementById('searchResults').replaceChildren();
  loadAclUsers(appName);
}}
function closeAclModal() {{
  document.getElementById('aclModal').classList.remove('active');
  loadMyApps();
}}

function buildAclItem(u, canRevoke) {{
  var li = document.createElement('li'); li.className = 'acl-item';
  var info = document.createElement('span'); info.className = 'user-info';
  info.textContent = u.grant_type + ': ' + u.grant_value + ' ';
  var team = document.createElement('span'); team.className = 'team';
  team.textContent = u.team_name || '';
  info.appendChild(team); li.appendChild(info);
  if (canRevoke) {{
    var btn = document.createElement('button'); btn.className = 'btn-sm danger';
    btn.textContent = '\ud68c\uc218';
    btn.onclick = function() {{ revokeAccess(u.id); }};
    li.appendChild(btn);
  }} else {{
    var btn = document.createElement('button'); btn.className = 'btn-sm';
    btn.style.borderColor = '#238636'; btn.style.color = '#3fb950';
    btn.textContent = '\ucd94\uac00';
    btn.onclick = function() {{ grantAccess(u.username); }};
    li.appendChild(btn);
  }}
  return li;
}}

function loadAclUsers(appName) {{
  apiFetch('/apps/' + appName + '/acl').then(function(data) {{
    var users = data.users || [];
    var list = document.getElementById('aclUserList');
    list.replaceChildren();
    if (users.length === 0) {{
      var empty = document.createElement('li'); empty.className = 'acl-item';
      empty.style.color = '#484f58'; empty.textContent = '\ud5c8\uc6a9\ub41c \uc0ac\uc6a9\uc790 \uc5c6\uc74c';
      list.appendChild(empty); return;
    }}
    users.forEach(function(u) {{ list.appendChild(buildAclItem(u, true)); }});
  }}).catch(function() {{}});
}}

function searchUsers() {{
  var q = document.getElementById('aclSearchInput').value.trim();
  if (!q) return;
  apiFetch('/files/org-members?q=' + encodeURIComponent(q)).then(function(data) {{
    var users = data.members || [];
    var el = document.getElementById('searchResults');
    el.replaceChildren();
    if (users.length === 0) {{
      var empty = document.createElement('div');
      empty.style.cssText = 'color:#484f58;font-size:0.82rem;padding:8px 0;';
      empty.textContent = '\uac80\uc0c9 \uacb0\uacfc \uc5c6\uc74c';
      el.appendChild(empty); return;
    }}
    var ul = document.createElement('ul'); ul.className = 'acl-list';
    users.forEach(function(u) {{
      var li = document.createElement('li'); li.className = 'acl-item';
      var info = document.createElement('span'); info.className = 'user-info';
      info.textContent = (u.name || u.username) + ' (' + u.username + ')';
      var meta = document.createElement('span'); meta.className = 'team';
      meta.textContent = ' ' + (u.job_name || '') + ' / ' + (u.team_name || '');
      info.appendChild(meta); li.appendChild(info);
      var btn = document.createElement('button'); btn.className = 'btn-sm';
      btn.style.borderColor = '#238636'; btn.style.color = '#3fb950';
      btn.textContent = '\ucd94\uac00';
      btn.onclick = function() {{ grantAccess(u.username); }};
      li.appendChild(btn);
      ul.appendChild(li);
    }});
    el.appendChild(ul);
  }}).catch(function() {{}});
}}

function grantAccess(username) {{
  apiFetch('/apps/' + currentAclApp + '/acl', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{grant_type: 'user', grant_value: username}})
  }}).then(function() {{
    loadAclUsers(currentAclApp);
    document.getElementById('searchResults').replaceChildren();
    document.getElementById('aclSearchInput').value = '';
  }});
}}

function revokeAccess(aclId) {{
  if (!confirm('\uc811\uadfc \uad8c\ud55c\uc744 \ud68c\uc218\ud569\ub2c8\ub2e4.')) return;
  apiFetch('/apps/' + currentAclApp + '/acl/' + aclId, {{ method: 'DELETE' }})
    .then(function() {{ loadAclUsers(currentAclApp); }});
}}

function onAclGrantTypeChange() {{
  var type = document.getElementById('aclGrantType').value;
  document.getElementById('aclUserPanel').style.display = (type === 'user') ? '' : 'none';
  document.getElementById('aclSelectPanel').style.display = (type !== 'user' && type !== 'company') ? '' : 'none';
  if (type === 'company') {{
    if (confirm('전사 공개로 설정합니다. 모든 임직원이 접근 가능해집니다.')) {{
      grantAccessByType();
    }}
    document.getElementById('aclGrantType').value = 'user';
    onAclGrantTypeChange();
    return;
  }}
  if (type !== 'user') {{ populateAclSelect(type); }}
}}

function populateAclSelect(type) {{
  var sel = document.getElementById('aclGrantValueSelect');
  sel.replaceChildren();
  var def = document.createElement('option'); def.value = ''; def.textContent = '선택하세요'; sel.appendChild(def);
  var items = [];
  if (aclOptions) {{
    if (type === 'team') items = aclOptions.teams || [];
    else if (type === 'region') items = aclOptions.regions || [];
    else if (type === 'job') items = aclOptions.jobs || [];
  }}
  items.forEach(function(o) {{
    var opt = document.createElement('option'); opt.value = o; opt.textContent = o; sel.appendChild(opt);
  }});
}}

function grantAccessByType() {{
  var type = document.getElementById('aclGrantType').value;
  var value = (type === 'company') ? '*' : document.getElementById('aclGrantValueSelect').value;
  if (type !== 'company' && !value) {{ return; }}
  apiFetch('/apps/' + currentAclApp + '/acl', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{grant_type: type, grant_value: value}})
  }}).then(function() {{
    loadAclUsers(currentAclApp);
    var sel = document.getElementById('aclGrantValueSelect');
    if (sel.options.length > 0) sel.selectedIndex = 0;
    var t = document.getElementById('hubToast');
    t.textContent = type + ' 접근 권한이 추가되었습니다.';
    t.style.display = 'block';
    setTimeout(function() {{ t.style.display = 'none'; }}, 2000);
  }}).catch(function() {{}});
}}

function undeployApp(appName) {{
  if (!confirm(appName + ' \uc571\uc744 \uc0ad\uc81c\ud569\ub2c8\ub2e4. \uc774 \uc791\uc5c5\uc740 \ub418\ub3cc\ub9b4 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.')) return;
  apiFetch('/apps/' + appName, {{ method: 'DELETE' }})
    .then(function() {{ loadMyApps(); }});
}}

function suspendApp(appName) {{
  if (!confirm(appName + ' \uc571\uc744 \ud68c\uc218\ud569\ub2c8\ub2e4. \uc11c\ube44\uc2a4\uac00 \uc911\ub2e8\ub418\uace0 \uc811\uadfc\uc774 \ucc28\ub2e8\ub429\ub2c8\ub2e4.\\nACL\uc740 \uc720\uc9c0\ub418\uba70, \uc7ac\ubc30\ud3ec\ub85c \ubcf5\uc6d0\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.')) return;
  apiFetch('/apps/' + appName + '/suspend', {{ method: 'POST' }})
    .then(function(data) {{
      if (data && data.suspended) {{
        var t = document.getElementById('hubToast');
        t.textContent = appName + ' \uc571\uc774 \ud68c\uc218\ub418\uc5c8\uc2b5\ub2c8\ub2e4.';
        t.style.display = 'block';
        setTimeout(function() {{ t.style.display = 'none'; }}, 2500);
      }}
      loadMyApps();
    }}).catch(function() {{}});
}}

function resumeApp(appName) {{
  apiFetch('/apps/' + appName + '/resume', {{ method: 'POST' }})
    .then(function(data) {{
      if (data && data.app_name) {{
        var t = document.getElementById('hubToast');
        t.textContent = appName + ' \uc571\uc774 \uc7ac\ubc30\ud3ec\ub418\uc5c8\uc2b5\ub2c8\ub2e4.';
        t.style.display = 'block';
        setTimeout(function() {{ t.style.display = 'none'; }}, 2500);
      }}
      loadMyApps();
    }}).catch(function() {{}});
}}

// ── 통계 모달 ──
function openStatsModal(appName) {{
  document.getElementById('statsAppName').textContent = appName;
  document.getElementById('statDau').textContent = '-';
  document.getElementById('statMau').textContent = '-';
  document.getElementById('statTotal').textContent = '-';
  document.getElementById('dauChart').replaceChildren();
  document.getElementById('visitorsBody').replaceChildren();
  document.getElementById('statsModal').classList.add('active');
  loadAppStats(appName);
}}
function closeStatsModal() {{ document.getElementById('statsModal').classList.remove('active'); }}

function loadAppStats(appName) {{
  apiFetch('/portal/apps/' + encodeURIComponent(appName) + '/stats').then(function(data) {{
    document.getElementById('statDau').textContent = data.dau_today || 0;
    document.getElementById('statMau').textContent = data.mau || 0;
    document.getElementById('statTotal').textContent = data.total_views || 0;
    renderStatsBarChart(data.dau_series || []);
    renderStatsVisitors(data.recent_visitors || []);
  }}).catch(function() {{
    var chart = document.getElementById('dauChart');
    chart.replaceChildren();
    var msg = document.createElement('div');
    msg.style.cssText = 'color:#484f58;font-size:0.82rem;text-align:center;padding:20px 0;';
    msg.textContent = '통계를 불러올 수 없습니다.';
    chart.appendChild(msg);
  }});
}}

function renderStatsBarChart(series) {{
  var chart = document.getElementById('dauChart');
  chart.replaceChildren();
  if (!series.length) {{
    var msg = document.createElement('div');
    msg.style.cssText = 'color:#484f58;font-size:0.82rem;text-align:center;padding:20px 0;';
    msg.textContent = 'DAU 데이터가 없습니다.';
    chart.appendChild(msg); return;
  }}
  var maxVal = 1;
  series.forEach(function(d) {{ if (d.visitors > maxVal) maxVal = d.visitors; }});
  series.forEach(function(d) {{
    var pct = Math.max((d.visitors / maxVal) * 100, 1.5);
    var group = document.createElement('div'); group.className = 'bar-group';
    var bar = document.createElement('div'); bar.className = 'bar'; bar.style.height = pct + '%';
    var tooltip = document.createElement('span'); tooltip.className = 'bar-tooltip';
    tooltip.textContent = d.visitors + '명';
    bar.appendChild(tooltip);
    var label = document.createElement('span'); label.className = 'bar-label';
    label.textContent = (d.date || '').slice(5);
    group.appendChild(bar); group.appendChild(label);
    chart.appendChild(group);
  }});
}}

function renderStatsVisitors(visitors) {{
  var tbody = document.getElementById('visitorsBody');
  tbody.replaceChildren();
  if (!visitors.length) {{
    var tr = document.createElement('tr');
    var td = document.createElement('td'); td.colSpan = 4;
    td.style.cssText = 'color:#484f58;font-size:0.82rem;text-align:center;padding:16px 0;';
    td.textContent = '방문 기록이 없습니다.';
    tr.appendChild(td); tbody.appendChild(tr); return;
  }}
  visitors.forEach(function(v) {{
    var tr = document.createElement('tr');
    var fields = [v.username || '-', v.name || '-', v.team_name || '-',
      v.visited_at ? v.visited_at.replace('T', ' ').slice(0, 16) : '-'];
    fields.forEach(function(text) {{
      var td = document.createElement('td'); td.textContent = text; tr.appendChild(td);
    }});
    tbody.appendChild(tr);
  }});
}}

// ── MMS 공유 모달 ──
var currentMmsApp = '';
function openMmsModal(appName) {{
  currentMmsApp = appName;
  document.getElementById('mmsAppName').textContent = appName;
  document.getElementById('mmsMessage').value = '';
  document.getElementById('mmsCharCount').textContent = '0';
  document.getElementById('mmsError').style.display = 'none';
  document.getElementById('mmsSuccess').style.display = 'none';
  var sendBtn = document.getElementById('mmsSendBtn');
  sendBtn.disabled = false; sendBtn.textContent = '발송';
  document.getElementById('mmsModal').classList.add('active');
}}
function closeMmsModal() {{ document.getElementById('mmsModal').classList.remove('active'); }}

function sendMmsFromHub() {{
  var msg = document.getElementById('mmsMessage').value.trim();
  if (!msg) {{
    document.getElementById('mmsError').textContent = '메시지를 입력해주세요.';
    document.getElementById('mmsError').style.display = 'block'; return;
  }}
  var sendBtn = document.getElementById('mmsSendBtn');
  sendBtn.disabled = true; sendBtn.textContent = '발송 중...';
  document.getElementById('mmsError').style.display = 'none';
  document.getElementById('mmsSuccess').style.display = 'none';

  apiFetch('/portal/apps/' + encodeURIComponent(currentMmsApp) + '/share-mms', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{message: msg}})
  }}).then(function(data) {{
    if (data && data.detail) {{
      var detail = data.detail;
      if (typeof detail === 'object' && detail.error === 'content_violation') {{
        document.getElementById('mmsError').textContent = '\\ud83d\\udeab ' + (detail.warning || '부적절한 내용이 감지되었습니다.');
        document.getElementById('mmsError').style.display = 'block';
        return;
      }}
      document.getElementById('mmsError').textContent = typeof detail === 'string' ? detail : 'MMS 발송에 실패했습니다.';
      document.getElementById('mmsError').style.display = 'block';
      return;
    }}
    var successEl = document.getElementById('mmsSuccess');
    successEl.textContent = '\u2705 ' + (data.sent || 0) + '명에게 MMS가 발송되었습니다.';
    if (data.blocked_users && data.blocked_users.length > 0) {{
      successEl.textContent += ' (발송 실패: ' + data.blocked_users.length + '명)';
    }}
    successEl.style.display = 'block';
    setTimeout(closeMmsModal, 2500);
  }}).catch(function(err) {{
    document.getElementById('mmsError').textContent = err.message || 'MMS 발송에 실패했습니다.';
    document.getElementById('mmsError').style.display = 'block';
  }}).finally(function() {{
    sendBtn.disabled = false; sendBtn.textContent = '발송';
  }});
}}

// ── 웹앱 공유(배포) 모달 ──
var deployAppNameVal = '';
var deployAppPathVal = '';
var deploySelectedUsernames = [];

function openDeployModal(appName, appPath) {{
  deployAppNameVal = appName;
  deployAppPathVal = appPath;
  deploySelectedUsernames = [];
  document.getElementById('deployAppName').textContent = appName;
  document.getElementById('deployUserSearch').value = '';
  document.getElementById('deploySearchResults').replaceChildren();
  document.getElementById('deploySelectedUsers').replaceChildren();
  var radios = document.querySelectorAll('input[name="deployVisibility"]');
  for (var i = 0; i < radios.length; i++) {{ if (radios[i].value === 'private') radios[i].checked = true; }}
  document.getElementById('deployAclSection').style.display = 'block';
  document.getElementById('deployModal').classList.add('active');
}}

function closeDeployModal() {{
  document.getElementById('deployModal').classList.remove('active');
}}

function searchDeployUsers() {{
  var q = document.getElementById('deployUserSearch').value.trim();
  if (!q) return;
  apiFetch('/files/org-members?q=' + encodeURIComponent(q)).then(function(data) {{
    var users = data.members || [];
    var el = document.getElementById('deploySearchResults');
    el.replaceChildren();
    users.forEach(function(u) {{
      var li = document.createElement('li'); li.className = 'acl-item';
      var info = document.createElement('span'); info.className = 'user-info';
      info.textContent = (u.name || u.username) + ' (' + u.username + ') ' + (u.team_name || '');
      li.appendChild(info);
      var btn = document.createElement('button'); btn.className = 'btn-sm';
      btn.style.borderColor = '#238636'; btn.style.color = '#3fb950';
      btn.textContent = '\ucd94\uac00';
      btn.onclick = function() {{
        if (deploySelectedUsernames.indexOf(u.username) === -1) {{
          deploySelectedUsernames.push(u.username);
          renderDeploySelected();
        }}
      }};
      li.appendChild(btn);
      el.appendChild(li);
    }});
  }}).catch(function() {{}});
}}

function renderDeploySelected() {{
  var el = document.getElementById('deploySelectedUsers');
  el.replaceChildren();
  deploySelectedUsernames.forEach(function(uname, i) {{
    var li = document.createElement('li'); li.className = 'acl-item';
    var info = document.createElement('span'); info.textContent = uname;
    li.appendChild(info);
    var btn = document.createElement('button'); btn.className = 'btn-sm danger';
    btn.textContent = '\uc81c\uac70';
    btn.onclick = function() {{
      deploySelectedUsernames.splice(i, 1);
      renderDeploySelected();
    }};
    li.appendChild(btn);
    el.appendChild(li);
  }});
}}

function executeDeploy() {{
  var t = document.getElementById('hubToast');
  t.textContent = '\ubc30\ud3ec \uc900\ube44 \uc911: ' + deployAppNameVal;
  t.style.display = 'block';

  // 1단계: 앱 코드 복사 (fileserver)
  localFetch('/api/apps/prepare-deploy', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{name: deployAppNameVal, path: deployAppPathVal}})
  }}).then(function(data) {{
    if (!data.prepared) {{
      t.textContent = '\ubc30\ud3ec \uc900\ube44 \uc2e4\ud328: ' + (data.error || '');
      t.style.background = '#da3633';
      setTimeout(function() {{ t.style.display = 'none'; t.style.background = ''; }}, 4000);
      return Promise.reject('prepare failed');
    }}
    // 2단계: 플랫폼 배포 API 호출
    t.textContent = '\ubc30\ud3ec \uc911: ' + deployAppNameVal;
    var visibility = document.querySelector('input[name="deployVisibility"]:checked').value;
    return apiFetch('/apps/deploy', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        app_name: deployAppNameVal,
        version: 'v-' + new Date().toISOString().slice(0,16).replace(/[-T:]/g, ''),
        visibility: visibility,
        app_port: 3000,
        acl_usernames: visibility === 'company' ? [] : deploySelectedUsernames
      }})
    }});
  }}).then(function(data) {{
    closeDeployModal();
    if (data && data.app_url) {{
      t.textContent = '\ubc30\ud3ec \uc644\ub8cc! URL: https://claude.skons.net' + data.app_url;
    }} else if (data && data.detail) {{
      t.textContent = '\ubc30\ud3ec \uc2e4\ud328: ' + data.detail;
      t.style.background = '#da3633';
    }}
    setTimeout(function() {{ t.style.display = 'none'; t.style.background = ''; }}, 5000);
    loadMyApps();
  }}).catch(function(err) {{
    if (err === 'prepare failed') return;
    t.textContent = '\ubc30\ud3ec \uc624\ub958: ' + (err.message || err);
    t.style.background = '#da3633';
    setTimeout(function() {{ t.style.display = 'none'; t.style.background = ''; }}, 5000);
  }});
}}

// ── 데이터 공유 관리 ──
var currentShareDataset = '';

function buildDatasetItem(ds, isOwner) {{
  var li = document.createElement('li'); li.className = 'app-item';
  li.style.flexWrap = 'wrap';
  var info = document.createElement('div'); info.className = 'app-info';
  var name = document.createElement('span'); name.className = 'app-name';
  name.style.cursor = 'default'; name.textContent = ds.dataset_name;
  var meta = document.createElement('div'); meta.className = 'app-meta';
  if (isOwner) {{
    var sizeStr = ds.file_size_bytes ? formatBytes(ds.file_size_bytes) : '';
    var parts = [];
    if (ds.file_type) parts.push(ds.file_type);
    if (ds.file_path) parts.push(ds.file_path);
    if (sizeStr) parts.push(sizeStr);
    var cnt = ds.acl_count || 0;
    parts.push(cnt > 0 ? cnt + '\uba85 \uacf5\uc720 \uc911' : '\ubbf8\uacf5\uc720');
    meta.textContent = parts.join(' \u00b7 ');
    // 공유 상태 배지
    if (cnt > 0) {{
      var badge = document.createElement('span');
      badge.style.cssText = 'display:inline-block;margin-left:8px;padding:1px 8px;border-radius:10px;font-size:0.7rem;background:#1a3a2a;color:#3fb950;';
      badge.textContent = cnt + '\uba85 \uacf5\uc720';
      name.appendChild(badge);
    }}
  }} else {{
    meta.textContent = (ds.owner_name || ds.owner_username || '') + ' \u00b7 ~/workspace/team/' + (ds.owner_username || '').toLowerCase() + '/' + (ds.dataset_name || '') + '/';
  }}
  if (ds.description) {{
    var desc = document.createElement('div');
    desc.style.cssText = 'font-size:0.72rem;color:#6e7681;margin-top:2px;';
    desc.textContent = ds.description;
    info.appendChild(name); info.appendChild(meta); info.appendChild(desc);
  }} else {{
    info.appendChild(name); info.appendChild(meta);
  }}
  li.appendChild(info);
  if (isOwner) {{
    var actions = document.createElement('div'); actions.className = 'app-actions';
    var shareBtn = document.createElement('button'); shareBtn.className = 'btn-sm';
    shareBtn.textContent = '\uacf5\uc720 \uad00\ub9ac';
    shareBtn.onclick = function() {{ openDataShareModal(ds.dataset_name); }};
    actions.appendChild(shareBtn); li.appendChild(actions);
  }}
  return li;
}}

function formatBytes(bytes) {{
  if (bytes < 1024) return bytes + 'B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB';
  if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + 'MB';
  return (bytes / 1024 / 1024 / 1024).toFixed(1) + 'GB';
}}

function loadMyDatasets() {{
  apiFetch('/files/datasets/my').then(function(data) {{
    // 백엔드가 배열 직접 반환 또는 {{datasets:[]}} 형태
    var datasets = Array.isArray(data) ? data : (data.datasets || []);
    var section = document.getElementById('myDatasetsSection');
    var list = document.getElementById('myDatasetsList');
    var count = document.getElementById('myDatasetsCount');
    count.textContent = '(' + datasets.length + ')';
    list.replaceChildren();
    if (datasets.length === 0) {{
      var empty = document.createElement('li'); empty.className = 'empty-msg';
      empty.textContent = '\uacf5\uc720 \ub370\uc774\ud130\uac00 \uc5c6\uc2b5\ub2c8\ub2e4. \uc704 "\ub370\uc774\ud130\uc14b \ub4f1\ub85d" \ubc84\ud2bc\uc73c\ub85c \ub4f1\ub85d\ud558\uc138\uc694.';
      list.appendChild(empty); return;
    }}
    datasets.forEach(function(ds) {{ list.appendChild(buildDatasetItem(ds, true)); }});
  }}).catch(function(e) {{ console.log('loadMyDatasets error:', e); }});
}}

function loadSharedDatasets() {{
  apiFetch('/files/datasets/shared').then(function(data) {{
    var datasets = Array.isArray(data) ? data : (data.datasets || []);
    // 공유 데이터셋 룩업 맵: 파일 탐색기 rel path → {{mount_id, file_path}}
    // 실제 pod 파일 경로는 team/{{owner_username(lower)}}/{{dataset_name}}/{{file_path}} 형식
    feSharedDatasetMap = {{}};
    datasets.forEach(function(ds) {{
      if (!ds || ds.id == null) return;
      var owner = (ds.owner_username || '').toLowerCase();
      var relKey = 'team/' + owner + '/' + (ds.dataset_name || '') + '/' + (ds.file_path || '');
      feSharedDatasetMap[relKey] = {{ mount_id: ds.id, file_path: ds.file_path || '' }};
    }});
    var section = document.getElementById('sharedDatasetsSection');
    var list = document.getElementById('sharedDatasetsList');
    var count = document.getElementById('sharedDatasetsCount');
    if (datasets.length === 0) {{ return; }}
    section.style.display = 'block';
    count.textContent = '(' + datasets.length + ')';
    list.replaceChildren();
    datasets.forEach(function(ds) {{ list.appendChild(buildDatasetItem(ds, false)); }});
  }}).catch(function() {{}});
}}

function openDataShareModal(datasetName) {{
  currentShareDataset = datasetName;
  document.getElementById('dataShareName').textContent = datasetName;
  document.getElementById('dataShareModal').classList.add('active');
  document.getElementById('shareUserSearch').value = '';
  document.getElementById('shareUserResults').replaceChildren();
  switchShareTab('user');
  loadDataShareUsers(datasetName);
  loadOrgOptions();
}}

function closeDataShareModal() {{
  document.getElementById('dataShareModal').classList.remove('active');
  loadMyDatasets();
  loadMyApps();
}}

function switchShareTab(tab) {{
  var tabs = ['user', 'region', 'team'];
  tabs.forEach(function(t) {{
    var btn = document.getElementById('shareTab' + t.charAt(0).toUpperCase() + t.slice(1));
    var panel = document.getElementById('share' + t.charAt(0).toUpperCase() + t.slice(1) + 'Panel');
    if (t === tab) {{
      btn.style.borderBottomColor = '#58a6ff'; btn.style.color = '#e6edf3';
      panel.style.display = 'block';
    }} else {{
      btn.style.borderBottomColor = 'transparent'; btn.style.color = '#8b949e';
      panel.style.display = 'none';
    }}
  }});
  // 담당/팀 선택 시 구성원 미리보기 초기화
  if (tab === 'region') {{ document.getElementById('regionMembers').replaceChildren(); }}
  if (tab === 'team') {{ document.getElementById('teamMembers').replaceChildren(); }}
}}

function loadOrgOptions() {{
  // 담당 목록
  apiFetch('/files/regions').then(function(data) {{
    var sel = document.getElementById('shareRegionSelect');
    while (sel.options.length > 1) sel.remove(1);
    (data.regions || []).forEach(function(r) {{
      var opt = document.createElement('option'); opt.value = r; opt.textContent = r;
      sel.appendChild(opt);
    }});
    sel.onchange = function() {{ if (sel.value) loadOrgMembers('region', sel.value); }};
  }}).catch(function() {{}});
  // 팀 목록
  apiFetch('/files/teams').then(function(data) {{
    var sel = document.getElementById('shareTeamSelect');
    while (sel.options.length > 1) sel.remove(1);
    (data.teams || []).forEach(function(t) {{
      var opt = document.createElement('option'); opt.value = t; opt.textContent = t;
      sel.appendChild(opt);
    }});
    sel.onchange = function() {{ if (sel.value) loadOrgMembers('team', sel.value); }};
  }}).catch(function() {{}});
}}

function loadOrgMembers(orgType, orgValue) {{
  var param = orgType === 'region' ? 'region=' : 'team=';
  apiFetch('/files/org-members?' + param + encodeURIComponent(orgValue)).then(function(data) {{
    var members = data.members || [];
    var container = document.getElementById(orgType + 'Members');
    container.replaceChildren();
    if (members.length === 0) {{
      var empty = document.createElement('div');
      empty.style.cssText = 'color:#484f58;font-size:0.82rem;padding:8px;';
      empty.textContent = '\uad6c\uc131\uc6d0 \uc5c6\uc74c';
      container.appendChild(empty); return;
    }}
    var label = document.createElement('div');
    label.style.cssText = 'font-size:0.78rem;color:#8b949e;margin-bottom:6px;';
    label.textContent = orgValue + ' \uad6c\uc131\uc6d0 (' + members.length + '\uba85)';
    container.appendChild(label);
    var ul = document.createElement('ul'); ul.className = 'acl-list';
    members.forEach(function(m) {{
      var li = document.createElement('li'); li.className = 'acl-item';
      var info = document.createElement('span'); info.className = 'user-info';
      info.textContent = (m.name || m.username) + ' (' + m.username + ')';
      var job = document.createElement('span'); job.className = 'team';
      job.textContent = ' ' + (m.job_name || '');
      info.appendChild(job); li.appendChild(info);
      var btn = document.createElement('button'); btn.className = 'btn-sm';
      btn.style.borderColor = '#238636'; btn.style.color = '#3fb950';
      btn.textContent = '\uac1c\uc778 \ucd94\uac00';
      btn.onclick = function() {{ grantDataShare(m.username, 'user'); }};
      li.appendChild(btn);
      ul.appendChild(li);
    }});
    container.appendChild(ul);
  }}).catch(function() {{}});
}}

function shareToOrg(orgType) {{
  var sel = document.getElementById(orgType === 'region' ? 'shareRegionSelect' : 'shareTeamSelect');
  var value = sel.value;
  if (!value) {{ alert('\uc870\uc9c1\uc744 \uc120\ud0dd\ud558\uc138\uc694.'); return; }}
  grantDataShare(value, 'team');
}}

function buildShareItem(s, canRevoke) {{
  var li = document.createElement('li'); li.className = 'acl-item';
  var info = document.createElement('span'); info.className = 'user-info';
  var target = s.share_target || '';
  if (s.share_type === 'team') {{
    info.textContent = target;
    var badge = document.createElement('span'); badge.className = 'team';
    badge.textContent = ' (\uc870\uc9c1)';
    info.appendChild(badge);
  }} else {{
    var displayName = s.target_name ? s.target_name + ' (' + target + ')' : target;
    info.textContent = displayName;
  }}
  li.appendChild(info);
  if (canRevoke) {{
    var btn = document.createElement('button'); btn.className = 'btn-sm danger';
    btn.textContent = '\ud68c\uc218';
    btn.onclick = function() {{ revokeDataShare(s.id); }};
    li.appendChild(btn);
  }}
  return li;
}}

function loadDataShareUsers(datasetName) {{
  apiFetch('/files/datasets/' + encodeURIComponent(datasetName) + '/share').then(function(data) {{
    var shares = Array.isArray(data) ? data : (data.shares || []);
    var list = document.getElementById('dataShareList');
    list.replaceChildren();
    if (shares.length === 0) {{
      var empty = document.createElement('li'); empty.className = 'acl-item';
      empty.style.color = '#484f58'; empty.textContent = '\uacf5\uc720 \ub300\uc0c1 \uc5c6\uc74c';
      list.appendChild(empty); return;
    }}
    shares.forEach(function(s) {{ list.appendChild(buildShareItem(s, true)); }});
  }}).catch(function() {{}});
}}

function searchShareUsers() {{
  var q = document.getElementById('shareUserSearch').value.trim();
  var job = document.getElementById('shareJobFilter').value;
  if (!q && !job) return;
  var params = [];
  if (q) params.push('q=' + encodeURIComponent(q));
  if (job) params.push('job=' + encodeURIComponent(job));
  apiFetch('/files/org-members?' + params.join('&')).then(function(data) {{
    var users = data.members || [];
    var el = document.getElementById('shareUserResults');
    el.replaceChildren();
    if (users.length === 0) {{
      var empty = document.createElement('div');
      empty.style.cssText = 'color:#484f58;font-size:0.82rem;padding:8px 0;';
      empty.textContent = '\uac80\uc0c9 \uacb0\uacfc \uc5c6\uc74c';
      el.appendChild(empty); return;
    }}
    var ul = document.createElement('ul'); ul.className = 'acl-list';
    users.forEach(function(u) {{
      var li = document.createElement('li'); li.className = 'acl-item';
      var info = document.createElement('span'); info.className = 'user-info';
      info.textContent = (u.name || u.username) + ' (' + u.username + ')';
      var meta = document.createElement('span'); meta.className = 'team';
      meta.textContent = ' ' + (u.job_name || '') + ' / ' + (u.team_name || '');
      info.appendChild(meta); li.appendChild(info);
      var btn = document.createElement('button'); btn.className = 'btn-sm';
      btn.style.borderColor = '#238636'; btn.style.color = '#3fb950';
      btn.textContent = '\ucd94\uac00';
      btn.onclick = function() {{ grantDataShare(u.username, 'user'); }};
      li.appendChild(btn);
      ul.appendChild(li);
    }});
    el.appendChild(ul);
  }}).catch(function() {{}});
}}

function grantDataShare(target, shareType) {{
  fetch('/api/v1/files/datasets/' + encodeURIComponent(currentShareDataset) + '/share', {{
    method: 'POST',
    headers: Object.assign({{'Content-Type': 'application/json'}}, authHeaders),
    body: JSON.stringify({{target: target, share_type: shareType}})
  }}).then(function(res) {{
    if (res.status === 409) {{
      var t = document.getElementById('hubToast');
      t.textContent = target + ' \uc740(\ub294) \uc774\ubbf8 \uacf5\uc720 \uc124\uc815\ub418\uc5b4 \uc788\uc2b5\ub2c8\ub2e4.';
      t.style.display = 'block'; t.style.borderColor = '#f59e0b';
      setTimeout(function() {{ t.style.display = 'none'; t.style.borderColor = '#58a6ff'; }}, 3000);
      return;
    }}
    if (!res.ok) {{ return res.text().then(function(t) {{ throw new Error(t); }}); }}
    loadDataShareUsers(currentShareDataset);
    // 결과 영역 초기화
    var sr = document.getElementById('shareUserResults');
    if (sr) sr.replaceChildren();
    var rm = document.getElementById('regionMembers');
    if (rm) rm.replaceChildren();
    var tm = document.getElementById('teamMembers');
    if (tm) tm.replaceChildren();
    // 성공 토스트
    var t = document.getElementById('hubToast');
    t.textContent = target + ' \uacf5\uc720 \ucd94\uac00 \uc644\ub8cc';
    t.style.display = 'block';
    setTimeout(function() {{ t.style.display = 'none'; }}, 2000);
  }}).catch(function(e) {{
    var t = document.getElementById('hubToast');
    t.textContent = '\uacf5\uc720 \uc2e4\ud328: ' + (e.message || '').substring(0, 80);
    t.style.display = 'block'; t.style.borderColor = '#da3633';
    setTimeout(function() {{ t.style.display = 'none'; t.style.borderColor = '#58a6ff'; }}, 3000);
  }});
}}

// shareToTeam은 shareToOrg로 대체됨 (위에서 정의)

function revokeDataShare(shareId) {{
  if (!confirm('\uacf5\uc720\ub97c \ud68c\uc218\ud569\ub2c8\ub2e4.')) return;
  apiFetch('/files/datasets/' + encodeURIComponent(currentShareDataset) + '/share/' + encodeURIComponent(shareId), {{
    method: 'DELETE'
  }}).then(function() {{ loadDataShareUsers(currentShareDataset); }});
}}

// ── 파일 브라우저 ──
var fileBrowserCallback = null;

function openFileBrowser(callback) {{
  fileBrowserCallback = callback;
  document.getElementById('fileBrowserModal').classList.add('active');
  document.getElementById('fileSelected').style.display = 'none';
  browseDirectory('');
}}

function closeFileBrowser() {{
  document.getElementById('fileBrowserModal').classList.remove('active');
}}

function browseDirectory(dirPath) {{
  fetch(fileserverBase + '/api/browse?path=' + encodeURIComponent(dirPath))
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      // 빵 부스러기 네비게이션
      var bc = document.getElementById('fileBreadcrumb');
      bc.replaceChildren();
      var rootLink = document.createElement('span');
      rootLink.textContent = 'workspace';
      rootLink.style.cursor = 'pointer';
      rootLink.onclick = function() {{ browseDirectory(''); }};
      bc.appendChild(rootLink);

      if (data.path) {{
        var parts = data.path.split('/');
        var accumulated = '';
        parts.forEach(function(part) {{
          accumulated = accumulated ? accumulated + '/' + part : part;
          var sep = document.createTextNode(' / ');
          bc.appendChild(sep);
          var link = document.createElement('span');
          link.textContent = part;
          link.style.cursor = 'pointer';
          var target = accumulated;
          link.onclick = function() {{ browseDirectory(target); }};
          bc.appendChild(link);
        }});
      }}

      // 파일 목록
      var list = document.getElementById('fileBrowserList');
      list.replaceChildren();

      // 상위 디렉토리
      if (data.path) {{
        var upItem = document.createElement('div');
        upItem.style.cssText = 'padding:8px 10px;border-bottom:1px solid #21262d;cursor:pointer;font-size:0.85rem;';
        upItem.textContent = '[..] \uc0c1\uc704';
        var parentPath = data.path.split('/').slice(0, -1).join('/');
        upItem.onclick = function() {{ browseDirectory(parentPath); }};
        upItem.onmouseover = function() {{ this.style.background = '#21262d'; }};
        upItem.onmouseout = function() {{ this.style.background = ''; }};
        list.appendChild(upItem);
      }}

      data.entries.forEach(function(entry) {{
        var item = document.createElement('div');
        item.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:8px 10px;border-bottom:1px solid #21262d;cursor:pointer;font-size:0.85rem;';
        item.onmouseover = function() {{ this.style.background = '#21262d'; }};
        item.onmouseout = function() {{ this.style.background = ''; }};

        var nameSpan = document.createElement('span');
        var icon = entry.type === 'dir' ? '[\ud3f4\ub354] ' : '[\ud30c\uc77c] ';
        nameSpan.textContent = icon + entry.name;
        nameSpan.style.color = entry.type === 'dir' ? '#58a6ff' : '#e6edf3';
        item.appendChild(nameSpan);

        if (entry.type === 'file') {{
          var sizeSpan = document.createElement('span');
          sizeSpan.style.cssText = 'color:#8b949e;font-size:0.75rem;';
          var kb = entry.size / 1024;
          sizeSpan.textContent = kb > 1024 ? (kb/1024).toFixed(1) + 'MB' : kb.toFixed(0) + 'KB';
          item.appendChild(sizeSpan);
        }}

        if (entry.type === 'dir') {{
          // 더블클릭: 디렉토리 진입 / 싱글클릭: 디렉토리 선택
          item.onclick = function() {{
            document.getElementById('fileSelected').style.display = 'block';
            document.getElementById('fileSelectedPath').textContent = entry.path + '/';
          }};
          item.ondblclick = function(e) {{
            e.preventDefault();
            browseDirectory(entry.path);
          }};
          // 진입 버튼 추가
          var enterBtn = document.createElement('button');
          enterBtn.textContent = '\uc5f4\uae30';
          enterBtn.style.cssText = 'padding:2px 8px;background:#21262d;border:1px solid #30363d;border-radius:4px;color:#58a6ff;font-size:0.72rem;cursor:pointer;';
          enterBtn.onclick = function(e) {{
            e.stopPropagation();
            browseDirectory(entry.path);
          }};
          item.appendChild(enterBtn);
        }} else {{
          item.onclick = function() {{
            document.getElementById('fileSelected').style.display = 'block';
            document.getElementById('fileSelectedPath').textContent = entry.path;
          }};
        }}

        list.appendChild(item);
      }});

      if (data.entries.length === 0) {{
        var empty = document.createElement('div');
        empty.style.cssText = 'padding:20px;text-align:center;color:#484f58;font-size:0.82rem;';
        empty.textContent = '\ube48 \ub514\ub809\ud1a0\ub9ac';
        list.appendChild(empty);
      }}
    }}).catch(function() {{
      // fileserver API가 다른 포트에서 동작 — 직접 호출
      var list = document.getElementById('fileBrowserList');
      list.replaceChildren();
      var err = document.createElement('div');
      err.style.cssText = 'padding:20px;text-align:center;color:#da3633;font-size:0.82rem;';
      err.textContent = '\ud30c\uc77c \ubaa9\ub85d\uc744 \ubd88\ub7ec\uc62c \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.';
      list.appendChild(err);
    }});
}}

function confirmFileSelection() {{
  var path = document.getElementById('fileSelectedPath').textContent;
  if (fileBrowserCallback) {{
    fileBrowserCallback(path);
  }}
  closeFileBrowser();
}}

// ── 데이터셋 등록 ──
function toggleRegisterForm() {{
  var form = document.getElementById('registerDatasetForm');
  form.style.display = form.style.display === 'none' ? 'block' : 'none';
}}

function registerDataset() {{
  var name = document.getElementById('regDatasetName').value.trim();
  var path = document.getElementById('regFilePath').value.trim();
  var type = document.getElementById('regFileType').value;
  var desc = document.getElementById('regDescription').value.trim();
  if (!name || !path) {{
    alert('\ub370\uc774\ud130\uc14b \uc774\ub984\uacfc \ud30c\uc77c \uacbd\ub85c\ub97c \uc785\ub825\ud558\uc138\uc694.');
    return;
  }}
  apiFetch('/files/datasets', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      dataset_name: name,
      file_path: path,
      file_type: type,
      description: desc || null,
      file_size_bytes: 0
    }})
  }}).then(function(data) {{
    document.getElementById('registerDatasetForm').style.display = 'none';
    document.getElementById('regDatasetName').value = '';
    document.getElementById('regFilePath').value = '';
    document.getElementById('regDescription').value = '';
    loadMyDatasets();
    // 등록 즉시 공유 모달 열기
    if (data && data.dataset_name) {{
      setTimeout(function() {{ openDataShareModal(data.dataset_name); }}, 500);
    }}
  }}).catch(function(e) {{
    alert('\ub4f1\ub85d \uc2e4\ud328: ' + (e.message || e));
  }});
}}

// ── 허브 페이지 네비게이션 ──
function openHubPage(pageId) {{
  document.querySelector('.cards').style.display = 'none';
  var banner = document.getElementById('announceBanner');
  if (banner) banner.style.display = 'none';
  // Hide all hub pages
  var pages = document.querySelectorAll('.hub-tab-content');
  for (var i = 0; i < pages.length; i++) pages[i].classList.remove('active');
  // Show target
  var target = document.getElementById('tab-' + pageId);
  if (target) target.classList.add('active');
  // Show back button
  document.getElementById('hubBackBtn').style.display = 'block';
  // Load content for the page
  if (pageId === 'skills') {{
    loadMySkills();
    loadSkillStore('popular');
    loadInstalledSkills();
  }}
  if (pageId === 'files') {{
    if (!feTable) initFileExplorer();
    else loadDirectory(feCurrentPath);
    loadMyDatasets();
    loadSharedDatasets();
  }}
  if (pageId === 'guide') {{
    loadGuides();
  }}
  if (pageId === 'apps') {{
    loadMyApps();
    loadSharedApps();
    loadMyShares();
    loadAclOptions();
  }}
}}

function backToHub() {{
  document.querySelector('.cards').style.display = '';
  var banner = document.getElementById('announceBanner');
  if (banner && !banner.dataset.dismissed) banner.style.display = '';
  var pages = document.querySelectorAll('.hub-tab-content');
  for (var i = 0; i < pages.length; i++) pages[i].classList.remove('active');
  document.getElementById('hubBackBtn').style.display = 'none';
}}

// ── 탭 전환 ──
function switchHubTab(tab) {{
  ['apps','files','skills','guide'].forEach(function(t) {{
    var el = document.getElementById('tab-' + t);
    if (t === tab) {{
      if (el) el.classList.add('active');
    }} else {{
      if (el) el.classList.remove('active');
    }}
  }});
  if (tab === 'skills') {{
    loadMySkills();
    loadSkillStore('popular');
    loadInstalledSkills();
  }}
  if (tab === 'files') {{
    if (!feTable) initFileExplorer();
    else loadDirectory(feCurrentPath);
    loadMyDatasets();
    loadSharedDatasets();
  }}
}}

// ── 가이드 ──
function loadGuides() {{
  var token = document.cookie.split('; ').find(function(c) {{ return c.startsWith('claude_token='); }});
  if (!token) return;
  token = token.split('=')[1];
  fetch('/api/v1/guides/', {{
    headers: {{ 'Authorization': 'Bearer ' + token }}
  }}).then(function(r) {{ return r.ok ? r.json() : null; }})
    .then(function(data) {{
      var container = document.getElementById('guidesList');
      var countEl = document.getElementById('guidesCount');
      while (container.firstChild) container.removeChild(container.firstChild);
      if (!data || !data.guides || data.guides.length === 0) {{
        var emptyMsg = document.createElement('p');
        emptyMsg.style.cssText = 'color:#888;font-size:13px';
        emptyMsg.textContent = '등록된 가이드가 없습니다.';
        container.appendChild(emptyMsg);
        countEl.textContent = '(0)';
        return;
      }}
      countEl.textContent = '(' + data.guides.length + ')';
      data.guides.forEach(function(g) {{
        var card = document.createElement('div');
        card.style.cssText = 'background:#252540;border:1px solid #333;border-radius:6px;padding:12px 16px;margin-bottom:8px;cursor:pointer';
        card.onclick = function() {{ showGuideDetail(g.id); }};
        var title = document.createElement('div');
        title.style.cssText = 'color:#eee;font-size:14px;font-weight:600';
        title.textContent = g.title;
        var meta = document.createElement('div');
        meta.style.cssText = 'color:#888;font-size:12px;margin-top:4px';
        meta.textContent = (g.author_name || g.author_username) + ' \u00b7 ' + (g.category || 'general') + ' \u00b7 조회 ' + (g.view_count || 0);
        card.appendChild(title);
        card.appendChild(meta);
        container.appendChild(card);
      }});
    }}).catch(function() {{}});
}}

function showGuideDetail(guideId) {{
  var token = document.cookie.split('; ').find(function(c) {{ return c.startsWith('claude_token='); }});
  if (!token) return;
  token = token.split('=')[1];
  fetch('/api/v1/guides/' + guideId, {{
    headers: {{ 'Authorization': 'Bearer ' + token }}
  }}).then(function(r) {{ return r.ok ? r.json() : null; }})
    .then(function(data) {{
      if (!data) return;
      var modal = document.getElementById('announceModal');
      document.getElementById('announceModalTitle').textContent = data.title;
      document.getElementById('announceModalBody').textContent = data.content;
      document.getElementById('announceModalDate').textContent = (data.author_name || data.author_username) + ' \u00b7 ' + new Date(data.created_at).toLocaleDateString('ko-KR');
      modal.style.display = 'block';
    }}).catch(function() {{}});
}}

// ── 공지사항 ──
function loadAnnouncements() {{
  var token = document.cookie.split('; ').find(function(c) {{ return c.startsWith('claude_token='); }});
  if (!token) return;
  token = token.split('=')[1];
  fetch('/api/v1/announcements/active', {{
    headers: {{ 'Authorization': 'Bearer ' + token }}
  }}).then(function(r) {{ return r.ok ? r.json() : null; }})
    .then(function(data) {{
      if (!data || !data.announcements || data.announcements.length === 0) return;
      var latest = data.announcements[0];
      var dismissed = localStorage.getItem('dismiss_announce_' + latest.id);
      if (dismissed) return;
      document.getElementById('announceBannerText').textContent = latest.title;
      document.getElementById('announceBanner').style.display = '';
      document.getElementById('announceBanner').dataset.announcement = JSON.stringify(latest);
    }}).catch(function() {{}});
}}

function showAnnouncementModal() {{
  var data = JSON.parse(document.getElementById('announceBanner').dataset.announcement || '{{}}');
  document.getElementById('announceModalTitle').textContent = data.title || '공지사항';
  document.getElementById('announceModalBody').textContent = data.content || '';
  document.getElementById('announceModalDate').textContent = data.created_at ? new Date(data.created_at).toLocaleDateString('ko-KR') : '';
  document.getElementById('announceModal').style.display = 'block';
}}

function closeAnnouncementModal() {{
  document.getElementById('announceModal').style.display = 'none';
}}

function dismissAnnouncement() {{
  var data = JSON.parse(document.getElementById('announceBanner').dataset.announcement || '{{}}');
  if (data.id) localStorage.setItem('dismiss_announce_' + data.id, '1');
  document.getElementById('announceBanner').style.display = 'none';
  document.getElementById('announceBanner').dataset.dismissed = '1';
}}

// ── 파일 탐색기 (Tabulator) ──
var feTable = null;
var feCurrentPath = '';
var feSharedDatasetMap = {{}};
var feContextTarget = null;

function getFileIcon(name, isDir) {{
  if (isDir) return '\\ud83d\\udcc1';
  var ext = (name.split('.').pop() || '').toLowerCase();
  var m = {{'py':'\\ud83d\\udc0d','js':'\\ud83d\\udcdc','ts':'\\ud83d\\udcd8','html':'\\ud83c\\udf10','css':'\\ud83c\\udfa8','json':'\\ud83d\\udccb','yaml':'\\ud83d\\udccb','md':'\\ud83d\\udcdd','txt':'\\ud83d\\udcdd','csv':'\\ud83d\\udcca','xlsx':'\\ud83d\\udcca','xls':'\\ud83d\\udcca','sql':'\\ud83d\\uddc4','png':'\\ud83d\\uddbc','jpg':'\\ud83d\\uddbc','svg':'\\ud83d\\uddbc','pdf':'\\ud83d\\udcd5','zip':'\\ud83d\\udce6','sh':'\\u2699','env':'\\ud83d\\udd12','lock':'\\ud83d\\udd12','java':'\\u2615','go':'\\ud83d\\udc39'}};
  return m[ext] || '\\ud83d\\udcc4';
}}
function formatFileSize(bytes) {{
  if (bytes == null || bytes === 0) return '\\u2014';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
  return (bytes / 1073741824).toFixed(2) + ' GB';
}}
function formatFileDate(dateStr) {{
  if (!dateStr) return '\\u2014';
  var d = new Date(dateStr);
  if (isNaN(d.getTime())) return '\\u2014';
  var y=d.getFullYear(),mo=String(d.getMonth()+1).padStart(2,'0'),dy=String(d.getDate()).padStart(2,'0');
  var h=d.getHours(),mi=String(d.getMinutes()).padStart(2,'0');
  var ap=h>=12?'\\uc624\\ud6c4':'\\uc624\\uc804';
  var h12=h%12; if(h12===0) h12=12;
  return y+'-'+mo+'-'+dy+' '+ap+' '+h12+':'+mi;
}}
function getFileType(name, isDir) {{
  if (isDir) return '\\ud30c\\uc77c \\ud3f4\\ub354';
  var ext = (name.split('.').pop() || '').toLowerCase();
  if (ext === name.toLowerCase()) return '\\ud30c\\uc77c';
  var t={{'py':'Python','js':'JavaScript','ts':'TypeScript','html':'HTML','css':'CSS','json':'JSON','yaml':'YAML','md':'Markdown','txt':'\\ud14d\\uc2a4\\ud2b8','csv':'CSV','xlsx':'Excel','sql':'SQL','pdf':'PDF','png':'PNG','jpg':'JPEG','svg':'SVG','zip':'ZIP','sh':'\\uc178 \\uc2a4\\ud06c\\ub9bd\\ud2b8'}};
  return t[ext] || ext.toUpperCase()+' \\ud30c\\uc77c';
}}

function updateBreadcrumb(path) {{
  var el = document.getElementById('fe-breadcrumb');
  if (!el) return;
  var parts = path ? path.split('/').filter(function(p){{return p;}}) : [];
  // Build breadcrumb via DOM (safe, no raw HTML injection)
  el.textContent = '';
  var home = document.createElement('span'); home.className='bc-home'; home.textContent='\\ud83c\\udfe0';
  home.onclick = function(){{loadDirectory('');}};
  el.appendChild(home);
  var sep0 = document.createElement('span'); sep0.className='bc-sep'; sep0.textContent='\\u203a';
  el.appendChild(sep0);
  if (parts.length === 0) {{
    var cur = document.createElement('span'); cur.className='bc-item current'; cur.textContent='workspace';
    el.appendChild(cur);
  }} else {{
    var wsLink = document.createElement('span'); wsLink.className='bc-item'; wsLink.textContent='workspace';
    wsLink.onclick = function(){{loadDirectory('');}};
    el.appendChild(wsLink);
    var acc = '';
    for (var i=0; i<parts.length; i++) {{
      acc = acc ? acc+'/'+parts[i] : parts[i];
      var s = document.createElement('span'); s.className='bc-sep'; s.textContent='\\u203a';
      el.appendChild(s);
      var item = document.createElement('span');
      if (i === parts.length-1) {{
        item.className='bc-item current'; item.textContent=parts[i];
      }} else {{
        item.className='bc-item'; item.textContent=parts[i];
        (function(target){{ item.onclick=function(){{loadDirectory(target);}}; }})(acc);
      }}
      el.appendChild(item);
    }}
  }}
}}

function initFileExplorer() {{
  feTable = new Tabulator('#file-table', {{
    layout: 'fitColumns', height: '460px',
    placeholder: '<div class="fe-empty"><div class="fe-empty-icon">&#128194;</div><div>\\uc774 \\ud3f4\\ub354\\ub294 \\ube44\\uc5b4 \\uc788\\uc2b5\\ub2c8\\ub2e4</div></div>',
    selectable: true, selectableRangeMode: 'click', headerSortTristate: true,
    columns: [
      {{ formatter:'rowSelection', titleFormatter:'rowSelection',
         titleFormatterParams:{{rowRange:'active'}},
         hozAlign:'center', headerHozAlign:'center', headerSort:false, width:40, frozen:true }},
      {{ title:'\\uc774\\ub984', field:'name', minWidth:250,
         formatter:function(cell){{
           var data=cell.getRow().getData(); var isDir=data.type==='dir';
           var div=document.createElement('div'); div.className='fe-name-cell'+(isDir?' is-dir':'');
           var iconSpan=document.createElement('span'); iconSpan.className='fe-icon'; iconSpan.textContent=getFileIcon(data.name,isDir);
           var label=document.createElement('span'); label.className='fe-label'; label.textContent=data.name;
           div.appendChild(iconSpan); div.appendChild(label); return div;
         }},
         sorter:function(a,b,aRow,bRow){{
           var ad=aRow.getData().type==='dir'?0:1, bd=bRow.getData().type==='dir'?0:1;
           if(ad!==bd) return ad-bd; return a.localeCompare(b);
         }},
         cellClick:function(e,cell){{
           var d=cell.getRow().getData();
           if(d.type==='dir') loadDirectory(d.path);
         }}
      }},
      {{ title:'\\uc218\\uc815\\ud55c \\ub0a0\\uc9dc', field:'mtime', width:180,
         formatter:function(cell){{return formatFileDate(cell.getValue());}}, sorter:'string' }},
      {{ title:'\\uc720\\ud615', field:'type_display', width:140,
         mutator:function(v,data){{return getFileType(data.name,data.type==='dir');}}, sorter:'string' }},
      {{ title:'\\ud06c\\uae30', field:'size', width:110, hozAlign:'right',
         formatter:function(cell){{
           if(cell.getRow().getData().type==='dir') return '\\u2014';
           return formatFileSize(cell.getValue());
         }},
         sorter:function(a,b,aRow,bRow){{
           if(aRow.getData().type==='dir'&&bRow.getData().type!=='dir') return -1;
           if(aRow.getData().type!=='dir'&&bRow.getData().type==='dir') return 1;
           return (a||0)-(b||0);
         }}
      }}
    ],
    rowDblClick:function(e,row){{
      var d=row.getData();
      if(d.type==='dir') {{ loadDirectory(d.path); return; }}
      openPreview(d);
    }},
    rowContext:function(e,row){{ e.preventDefault(); feContextTarget=row.getData(); row.select(); showContextMenu(e.pageX,e.pageY); }}
  }});

  // DOM 레벨 이벤트 보강 — Tabulator 이벤트가 브라우저에서 동작하지 않는 경우 대비
  var tableEl = document.getElementById('file-table');
  if (tableEl) {{
    tableEl.addEventListener('contextmenu', function(e) {{
      var row = feTable.getRow(e.target.closest('.tabulator-row'));
      if (row) {{
        e.preventDefault();
        e.stopPropagation();
        feContextTarget = row.getData();
        row.select();
        showContextMenu(e.pageX, e.pageY);
      }}
    }});
    tableEl.addEventListener('dblclick', function(e) {{
      var row = feTable.getRow(e.target.closest('.tabulator-row'));
      if (row) {{
        var d = row.getData();
        if (d.type === 'dir') {{ loadDirectory(d.path); return; }}
        openPreview(d);
      }}
    }});
  }}

  loadDirectory('');
}}

function openPreview(d) {{
  var ext = getFileExt(d.name);
  var username = '{user_id}';
  if (MARKDOWN_EXTENSIONS[ext]) {{
    window.open('/api/v1/viewers/markdown/' + encodeURIComponent(username) + '/' + encodeURIComponent(d.path), '_blank');
  }} else if (OFFICE_EXTENSIONS[ext]) {{
    window.open('/api/v1/viewers/onlyoffice/' + encodeURIComponent(username) + '/' + encodeURIComponent(d.path), '_blank');
  }} else if (PREVIEW_EXTENSIONS[ext]) {{
    window.open('/api/v1/viewers/file/' + encodeURIComponent(username) + '/' + encodeURIComponent(d.path), '_blank');
  }}
}}

function loadDirectory(path) {{
  feCurrentPath = path || '';
  updateBreadcrumb(feCurrentPath);
  fetch(fileserverBase + '/api/browse?path=' + encodeURIComponent(feCurrentPath))
    .then(function(r){{return r.json();}})
    .then(function(data){{
      var entries = data.entries || [];
      entries.sort(function(a,b){{
        if(a.type==='dir'&&b.type!=='dir') return -1;
        if(a.type!=='dir'&&b.type==='dir') return 1;
        return a.name.localeCompare(b.name);
      }});
      if(feTable) feTable.setData(entries);
    }}).catch(function(){{ if(feTable) feTable.clearData(); }});
}}

function navigateUp() {{
  if (!feCurrentPath) return;
  var parts = feCurrentPath.split('/').filter(function(p){{return p;}});
  parts.pop(); loadDirectory(parts.join('/'));
}}

function uploadFilesExplorer(files) {{
  if (!files||files.length===0) return;
  var formData = new FormData();
  for (var i=0;i<files.length;i++) formData.append('files',files[i]);
  fetch(fileserverBase + '/upload', {{ method:'POST', body:formData }})
    .then(function(r){{return r.json();}})
    .then(function(){{ loadDirectory(feCurrentPath); }})
    .catch(function(err){{ alert('\\uc5c5\\ub85c\\ub4dc \\uc2e4\\ud328: '+err); }});
  document.getElementById('fe-upload-input').value = '';
}}

function deleteSelectedFiles() {{
  if (!feTable) return;
  var selected = feTable.getSelectedData();
  if (selected.length===0) {{ alert('\\uc0ad\\uc81c\\ud560 \\ud30c\\uc77c\\uc744 \\uc120\\ud0dd\\ud558\\uc138\\uc694.'); return; }}
  var names = selected.map(function(s){{return s.name;}}).join(', ');
  if (!confirm(selected.length+'\\uac1c \\ud56d\\ubaa9 \\uc0ad\\uc81c?\\n\\n'+names)) return;
  var promises = selected.map(function(item){{
    var p = feCurrentPath ? feCurrentPath+'/'+item.name : item.name;
    return fetch(fileserverBase + '/api/delete', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{path:p}})
    }});
  }});
  Promise.all(promises).then(function(){{loadDirectory(feCurrentPath);}}).catch(function(){{loadDirectory(feCurrentPath);}});
}}

function createNewFolder() {{
  var name = prompt('\\uc0c8 \\ud3f4\\ub354 \\uc774\\ub984:');
  if (!name) return;
  var dirPath = feCurrentPath ? feCurrentPath+'/'+name : name;
  fetch(fileserverBase + '/api/mkdir', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{path:dirPath}})
  }}).then(function(){{ loadDirectory(feCurrentPath); }})
    .catch(function(err){{ alert('\\ud3f4\\ub354 \\uc0dd\\uc131 \\uc2e4\\ud328: '+err); }});
}}

function downloadFileExplorer(filePath) {{
  var a = document.createElement('a');
  a.href = fileserverBase + '/api/download?path='+encodeURIComponent(filePath);
  a.download = ''; document.body.appendChild(a); a.click(); document.body.removeChild(a);
}}

function renameFileExplorer(filePath, oldName) {{
  var newName = prompt('\\uc0c8 \\uc774\\ub984:', oldName);
  if (!newName || newName===oldName) return;
  fetch(fileserverBase + '/api/rename', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{old_path:filePath, new_name:newName}})
  }}).then(function(r){{return r.json();}})
    .then(function(data){{
      if(data.error) alert('\\uc774\\ub984 \\ubcc0\\uacbd \\uc2e4\\ud328: '+data.error);
      else loadDirectory(feCurrentPath);
    }}).catch(function(err){{ alert('\\uc624\\ub958: '+err); }});
}}

// Context menu
function showContextMenu(x,y) {{
  var menu=document.getElementById('fe-context-menu');
  // 대상 파일 유형에 따라 "편집"/"함께 편집" 메뉴 표시 여부 갱신
  var previewItem = document.getElementById('ctxPreviewItem');
  var previewLabel = document.getElementById('ctxPreviewLabel');
  var editItem = document.getElementById('ctxEditItem');
  var coEditItem = document.getElementById('ctxCoEditItem');
  var isOfficeFile = false, isShared = false;
  if (feContextTarget && feContextTarget.type === 'file') {{
    var ext = getFileExt(feContextTarget.name);
    isOfficeFile = !!OFFICE_EXTENSIONS[ext];
    if (isOfficeFile) isShared = !!getSharedMountInfo(feContextTarget.path);
  }}
  if (previewItem) previewItem.style.display = '';
  if (previewLabel) previewLabel.textContent = isOfficeFile ? ' 열기 (보기 전용)' : ' 미리보기';
  if (editItem) editItem.style.display = isOfficeFile ? '' : 'none';
  if (coEditItem) coEditItem.style.display = (isOfficeFile && isShared) ? '' : 'none';
  menu.style.left=x+'px'; menu.style.top=y+'px'; menu.classList.add('visible');
  var rect=menu.getBoundingClientRect();
  if(rect.right>window.innerWidth) menu.style.left=(x-rect.width)+'px';
  if(rect.bottom>window.innerHeight) menu.style.top=(y-rect.height)+'px';
}}

// 파일의 rel path가 공유 데이터셋인지 확인 → {{mount_id, file_path}} 반환
function getSharedMountInfo(relPath) {{
  if (!relPath || relPath.indexOf('team/') !== 0) return null;
  return feSharedDatasetMap[relPath] || null;
}}
function hideContextMenu() {{
  var menu=document.getElementById('fe-context-menu');
  if(menu) menu.classList.remove('visible'); feContextTarget=null;
}}
document.addEventListener('click', function(){{ hideContextMenu(); }});
document.addEventListener('keydown', function(e){{ if(e.key==='Escape') hideContextMenu(); }});
var PREVIEW_EXTENSIONS = {{'pdf':1,'png':1,'jpg':1,'jpeg':1,'gif':1,'svg':1,'txt':1}};
var OFFICE_EXTENSIONS = {{'xlsx':1,'xls':1,'csv':1,'docx':1,'doc':1,'pptx':1,'ppt':1}};
var MARKDOWN_EXTENSIONS = {{'md':1,'markdown':1}};

function getFileExt(name) {{
  var parts = name.split('.');
  return parts.length > 1 ? parts.pop().toLowerCase() : '';
}}

function ctxPreview() {{
  if (!feContextTarget) return;
  var ext = getFileExt(feContextTarget.name);
  if (MARKDOWN_EXTENSIONS[ext] || OFFICE_EXTENSIONS[ext] || PREVIEW_EXTENSIONS[ext]) {{
    openPreview(feContextTarget);
  }} else {{
    alert('이 파일 형식은 미리보기를 지원하지 않습니다.');
  }}
  hideContextMenu();
}}

function ctxEdit() {{
  if(!feContextTarget || feContextTarget.type!=='file') {{ hideContextMenu(); return; }}
  var ext = getFileExt(feContextTarget.name);
  if(!OFFICE_EXTENSIONS[ext]) {{ hideContextMenu(); return; }}
  var username = '{user_id}';
  var url = '/api/v1/viewers/onlyoffice/edit/' + encodeURIComponent(username) + '/' + encodeURIComponent(feContextTarget.path);
  window.open(url, '_blank');
  hideContextMenu();
}}

function ctxCoEdit() {{
  if(!feContextTarget || feContextTarget.type!=='file') {{ hideContextMenu(); return; }}
  var info = getSharedMountInfo(feContextTarget.path);
  if(!info) {{ hideContextMenu(); return; }}
  var url = '/api/v1/viewers/onlyoffice/shared/' + encodeURIComponent(info.mount_id) + '/' + encodeURIComponent(info.file_path);
  window.open(url, '_blank');
  hideContextMenu();
}}

function ctxOpen() {{
  if(!feContextTarget) return;
  if(feContextTarget.type==='dir') loadDirectory(feContextTarget.path);
  else downloadFileExplorer(feContextTarget.path);
  hideContextMenu();
}}
function ctxDownload() {{ if(!feContextTarget) return; downloadFileExplorer(feContextTarget.path); hideContextMenu(); }}
function ctxRename() {{ if(!feContextTarget) return; renameFileExplorer(feContextTarget.path,feContextTarget.name); hideContextMenu(); }}
function ctxCopyPath() {{
  if(!feContextTarget) return;
  if(navigator.clipboard) navigator.clipboard.writeText(feContextTarget.path);
  hideContextMenu();
}}
function ctxDelete() {{
  if(!feContextTarget) return;
  if(!confirm('"'+feContextTarget.name+'" \\uc0ad\\uc81c?')) {{ hideContextMenu(); return; }}
  fetch(fileserverBase + '/api/delete', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{path:feContextTarget.path}})
  }}).then(function(){{ loadDirectory(feCurrentPath); }});
  hideContextMenu();
}}
// 키보드 단축키 (파일 탭 활성 시)
document.addEventListener('keydown', function(e) {{
  var tab=document.getElementById('tab-files');
  if(!tab||!tab.classList.contains('active')) return;
  if(e.key==='Backspace'&&!e.target.matches('input,textarea')){{ e.preventDefault(); navigateUp(); }}
  if(e.key==='Delete'&&!e.target.matches('input,textarea')) deleteSelectedFiles();
  if(e.key==='F2'&&feTable){{ var s=feTable.getSelectedData(); if(s.length===1) renameFileExplorer(s[0].path,s[0].name); }}
  if(e.key==='Enter'&&!e.target.matches('input,textarea')&&feTable){{ var s=feTable.getSelectedData(); if(s.length===1&&s[0].type==='dir') loadDirectory(s[0].path); }}
  if(e.key==='a'&&(e.ctrlKey||e.metaKey)&&!e.target.matches('input,textarea')){{ e.preventDefault(); if(feTable) feTable.selectRow(); }}
}});

// ── 앱 상태 통합 로드 ──
function loadAppStatus() {{
  localFetch('/api/apps/status').then(function(data) {{
    var running = data.running || [];
    var count = running.length;
    // 웹앱 카드가 제거되었으므로 myAppsList 업데이트만 수행
    // (loadMyApps가 앱 상태를 반영함)
  }}).catch(function() {{}});
}}

function buildUnifiedAppItem(app) {{
  var li = document.createElement('li'); li.className = 'app-item'; li.style.flexWrap = 'wrap';
  var info = document.createElement('div'); info.className = 'app-info';
  var nameRow = document.createElement('div'); nameRow.style.display = 'flex'; nameRow.style.alignItems = 'center'; nameRow.style.gap = '8px';
  var nameEl = document.createElement('span'); nameEl.className = 'app-name'; nameEl.style.cursor = 'default';
  nameEl.textContent = app.name || app.app_name || '';
  nameRow.appendChild(nameEl);
  // Edit button (로컬 앱만)
  if (!app.is_platform) {{
    var editBtn = document.createElement('span');
    editBtn.textContent = '\u270f\ufe0f';
    editBtn.style.cssText = 'cursor:pointer;font-size:0.75rem;';
    editBtn.title = '이름 변경';
    editBtn.onclick = function() {{ renameApp(app.name || app.app_name); }};
    nameRow.appendChild(editBtn);
  }}
  // Status badge
  var badge = document.createElement('span'); badge.className = 'status-badge';
  var status = app.status || 'stopped';
  if (status === 'deployed') {{ badge.className += ' status-deployed'; badge.textContent = '배포됨'; }}
  else if (status === 'suspended') {{ badge.className += ' status-suspended'; badge.textContent = '회수됨'; }}
  else if (status === 'running') {{ badge.className += ' status-running'; badge.textContent = '실행중'; }}
  else if (status === 'deleted') {{ badge.className += ' status-deleted'; badge.textContent = '삭제됨'; }}
  else {{ badge.className += ' status-stopped'; badge.textContent = '미실행'; }}
  nameRow.appendChild(badge);
  info.appendChild(nameRow);
  // DAU/MAU/ACL 뱃지 (플랫폼 앱만)
  if (app.is_platform) {{
    var badgeRow = document.createElement('div'); badgeRow.style.cssText = 'margin-top:4px;display:flex;gap:6px;';
    var metrics = [['DAU', app.dau], ['MAU', app.mau], ['ACL', app.acl_count]];
    metrics.forEach(function(m) {{
      var mb = document.createElement('span'); mb.className = 'metric-badge';
      mb.textContent = m[0] + ' ';
      var v = document.createElement('span'); v.textContent = (m[1] || 0);
      mb.appendChild(v);
      badgeRow.appendChild(mb);
    }});
    info.appendChild(badgeRow);
  }}
  var meta = document.createElement('div'); meta.className = 'app-meta';
  var parts = [];
  if (app.version) parts.push(app.version);
  if (status === 'running' && app.port) parts.push('포트 ' + app.port);
  if (app.path) parts.push(app.path.replace('/home/node/workspace/', '~/'));
  meta.textContent = parts.join(' \u00b7 ');
  info.appendChild(meta);
  li.appendChild(info);
  // Action buttons per state
  var actions = document.createElement('div'); actions.className = 'app-actions'; actions.style.flexWrap = 'wrap';
  if (status === 'deployed') {{
    var openBtn = document.createElement('a'); openBtn.className = 'btn-sm';
    openBtn.href = app.app_url || '#'; openBtn.target = '_blank'; openBtn.textContent = '열기';
    openBtn.style.borderColor = '#58a6ff'; openBtn.style.color = '#58a6ff'; openBtn.style.textDecoration = 'none';
    actions.appendChild(openBtn);
    if (app.is_platform) {{
      var statsBtn = document.createElement('button'); statsBtn.className = 'btn-sm';
      statsBtn.style.borderColor = '#d29922'; statsBtn.style.color = '#d29922';
      statsBtn.textContent = '통계';
      statsBtn.onclick = function() {{ openStatsModal(app.app_name); }};
      actions.appendChild(statsBtn);
      var aclBtn = document.createElement('button'); aclBtn.className = 'btn-sm';
      aclBtn.textContent = '접근 관리';
      aclBtn.onclick = function() {{ openAclModal(app.app_name); }};
      actions.appendChild(aclBtn);
      var mmsBtn = document.createElement('button'); mmsBtn.className = 'btn-sm';
      mmsBtn.style.borderColor = '#a371f7'; mmsBtn.style.color = '#a371f7';
      mmsBtn.textContent = 'MMS';
      mmsBtn.onclick = function() {{ openMmsModal(app.app_name); }};
      actions.appendChild(mmsBtn);
      var suspendBtn = document.createElement('button'); suspendBtn.className = 'btn-sm';
      suspendBtn.style.borderColor = '#d29922'; suspendBtn.style.color = '#d29922';
      suspendBtn.textContent = '회수';
      suspendBtn.onclick = function() {{ suspendApp(app.app_name); }};
      actions.appendChild(suspendBtn);
      var delBtn = document.createElement('button'); delBtn.className = 'btn-sm danger';
      delBtn.textContent = '삭제';
      delBtn.onclick = function() {{ undeployApp(app.app_name); }};
      actions.appendChild(delBtn);
    }}
  }} else if (status === 'suspended' && app.is_platform) {{
    var resumeBtn = document.createElement('button'); resumeBtn.className = 'btn-sm';
    resumeBtn.style.borderColor = '#238636'; resumeBtn.style.color = '#3fb950';
    resumeBtn.textContent = '재배포';
    resumeBtn.onclick = function() {{ resumeApp(app.app_name); }};
    actions.appendChild(resumeBtn);
    var aclBtn2 = document.createElement('button'); aclBtn2.className = 'btn-sm';
    aclBtn2.textContent = '접근 관리';
    aclBtn2.onclick = function() {{ openAclModal(app.app_name); }};
    actions.appendChild(aclBtn2);
    var delBtn2 = document.createElement('button'); delBtn2.className = 'btn-sm danger';
    delBtn2.textContent = '삭제';
    delBtn2.onclick = function() {{ undeployApp(app.app_name); }};
    actions.appendChild(delBtn2);
  }} else if (status === 'running') {{
    var openBtn = document.createElement('a'); openBtn.className = 'btn-sm';
    openBtn.href = app.app_url || '#'; openBtn.target = '_blank'; openBtn.textContent = '열기';
    openBtn.style.borderColor = '#58a6ff'; openBtn.style.color = '#58a6ff'; openBtn.style.textDecoration = 'none';
    actions.appendChild(openBtn);
    var shareBtn = document.createElement('button'); shareBtn.className = 'btn-sm';
    shareBtn.style.borderColor = '#a371f7'; shareBtn.style.color = '#a371f7';
    shareBtn.textContent = '\uacf5\uc720';
    shareBtn.onclick = function() {{ openDeployModal(app.name, app.path); }};
    actions.appendChild(shareBtn);
    var stopBtn = document.createElement('button'); stopBtn.className = 'btn-sm danger';
    stopBtn.textContent = '중지';
    stopBtn.onclick = function() {{ stopApp(app.port); }};
    actions.appendChild(stopBtn);
  }} else if (status === 'stopped') {{
    var startBtn = document.createElement('button'); startBtn.className = 'btn-sm';
    startBtn.style.borderColor = '#238636'; startBtn.style.color = '#3fb950';
    startBtn.textContent = '실행';
    startBtn.onclick = function() {{ startApp(app.path, app.type); }};
    actions.appendChild(startBtn);
    var shareBtn2 = document.createElement('button'); shareBtn2.className = 'btn-sm';
    shareBtn2.style.borderColor = '#a371f7'; shareBtn2.style.color = '#a371f7';
    shareBtn2.textContent = '\uacf5\uc720';
    shareBtn2.onclick = function() {{ openDeployModal(app.name, app.path); }};
    actions.appendChild(shareBtn2);
    var delProjBtn = document.createElement('button'); delProjBtn.className = 'btn-sm danger';
    delProjBtn.textContent = '삭제';
    delProjBtn.onclick = function() {{ deleteProject(app.path); }};
    actions.appendChild(delProjBtn);
  }} else if (status === 'deleted') {{
    var verBtn = document.createElement('button'); verBtn.className = 'btn-sm';
    verBtn.textContent = '버전 이력';
    verBtn.onclick = function() {{ openVersionModal(app.app_name); }};
    actions.appendChild(verBtn);
  }}
  li.appendChild(actions);
  return li;
}}

function stopAllApps() {{
  if (!confirm('실행 중인 모든 앱을 중지합니다.')) return;
  localFetch('/api/apps/stop-all', {{ method: 'POST' }}).then(function() {{
    loadMyApps();
    loadAppStatus();
    var t = document.getElementById('hubToast');
    t.textContent = '모든 앱이 중지되었습니다.';
    t.style.display = 'block';
    setTimeout(function() {{ t.style.display = 'none'; }}, 2000);
  }}).catch(function() {{}});
}}

function startApp(path, type) {{
  var t = document.getElementById('hubToast');
  t.textContent = '앱 시작 중: ' + path.split('/').pop();
  t.style.display = 'block';
  localFetch('/api/apps/start', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{path: path, type: type || 'node'}})
  }}).then(function(data) {{
    if (data.started) {{
      var appUrl = fileserverBase + '/webapp/' + data.port + '/';
      t.textContent = '앱 시작됨 (포트 ' + data.port + ') — 새 탭에서 열기 중...';
      setTimeout(function() {{
        window.open(appUrl, '_blank');
        t.style.display = 'none';
        loadMyApps();
        loadAppStatus();
      }}, 2000);
    }} else {{
      t.textContent = '시작 실패: ' + (data.error || '알 수 없는 오류');
      setTimeout(function() {{ t.style.display = 'none'; }}, 3000);
    }}
  }}).catch(function(err) {{
    t.textContent = '앱 시작 오류: ' + (err.message || err);
    t.style.background = '#da3633';
    setTimeout(function() {{ t.style.display = 'none'; t.style.background = ''; }}, 5000);
  }});
}}

function stopApp(port) {{
  var t = document.getElementById('hubToast');
  t.textContent = '앱 종료 중 (포트 ' + port + ')...';
  t.style.display = 'block';
  localFetch('/api/apps/stop', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{port: port}})
  }}).then(function() {{
    t.textContent = '앱 종료됨 (포트 ' + port + ')';
    setTimeout(function() {{ t.style.display = 'none'; }}, 2000);
    loadMyApps();
    loadAppStatus();
  }}).catch(function() {{ t.style.display = 'none'; }});
}}

function renameApp(oldName) {{
  var newName = prompt('새 이름을 입력하세요:', oldName);
  if (!newName || newName === oldName) return;
  localFetch('/api/apps/rename', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{old_name: oldName, new_name: newName}})
  }}).then(function() {{
    loadMyApps();
    var t = document.getElementById('hubToast');
    t.textContent = '이름이 변경되었습니다: ' + newName;
    t.style.display = 'block';
    setTimeout(function() {{ t.style.display = 'none'; }}, 2000);
  }}).catch(function() {{}});
}}

function deleteProject(path) {{
  var appName = path.split('/').pop();
  var msg = '⚠️ 경고: 코드 파일이 영구 삭제됩니다!\\n\\n';
  msg += '삭제 대상: ' + path + '\\n';
  msg += '이 작업은 되돌릴 수 없습니다.\\n\\n';
  msg += '삭제하려면 앱 이름을 정확히 입력하세요:';
  var input = prompt(msg);
  if (input !== appName) {{
    if (input !== null) {{
      var t = document.getElementById('hubToast');
      t.textContent = '앱 이름이 일치하지 않습니다: "' + appName + '"을 입력해야 합니다.';
      t.style.background = '#da3633';
      t.style.display = 'block';
      setTimeout(function() {{ t.style.display = 'none'; t.style.background = ''; }}, 3000);
    }}
    return;
  }}
  localFetch('/api/apps/delete-project', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{path: path}})
  }}).then(function(data) {{
    var t = document.getElementById('hubToast');
    if (data.error) {{
      t.textContent = '삭제 실패: ' + data.error;
      t.style.background = '#da3633';
    }} else {{
      t.textContent = appName + ' 삭제 완료';
    }}
    t.style.display = 'block';
    setTimeout(function() {{ t.style.display = 'none'; t.style.background = ''; }}, 3000);
    loadMyApps();
  }}).catch(function() {{}});
}}

// ── 버전 이력 모달 ──
function openVersionModal(appName) {{
  localFetch('/api/apps/versions/' + encodeURIComponent(appName)).then(function(data) {{
    var versions = data.versions || [];
    var msg = appName + ' 버전 이력:\\n\\n';
    if (versions.length === 0) {{
      msg += '버전 이력이 없습니다.';
    }} else {{
      versions.forEach(function(v, i) {{
        msg += (i + 1) + '. ' + v.version + ' (' + v.date + ')';
        if (v.is_current) msg += ' [현재]';
        msg += '\\n';
      }});
      msg += '\\n복원할 버전 번호를 입력하세요 (취소: 빈 값):';
    }}
    var choice = prompt(msg);
    if (choice && versions[parseInt(choice) - 1]) {{
      restoreVersion(appName, versions[parseInt(choice) - 1].version);
    }}
  }}).catch(function() {{}});
}}

function restoreVersion(appName, version) {{
  if (!confirm(appName + '을 ' + version + ' 버전으로 복원합니다.')) return;
  apiFetch('/apps/' + encodeURIComponent(appName) + '/restore', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{version: version}})
  }}).then(function() {{
    loadMyApps();
    var t = document.getElementById('hubToast');
    t.textContent = appName + ' → ' + version + ' 복원 완료';
    t.style.display = 'block';
    setTimeout(function() {{ t.style.display = 'none'; }}, 2000);
  }}).catch(function() {{}});
}}

// ── 내 공유 관리 ──
function loadMyShares() {{
  apiFetch('/apps/my-shares').then(function(data) {{
    var shares = data.shares || [];
    var list = document.getElementById('mySharesList');
    var count = document.getElementById('mySharesCount');
    var revokeBtn = document.getElementById('bulkRevokeBtn');
    count.textContent = '(' + shares.length + ')';
    list.replaceChildren();
    if (shares.length === 0) {{
      var empty = document.createElement('li'); empty.className = 'empty-msg';
      empty.textContent = '공유 항목이 없습니다.';
      list.appendChild(empty);
      revokeBtn.style.display = 'none';
      return;
    }}
    revokeBtn.style.display = 'inline-block';
    shares.forEach(function(s) {{
      var li = document.createElement('li'); li.className = 'app-item';
      li.style.gap = '8px';
      var check = document.createElement('input'); check.type = 'checkbox';
      check.className = 'share-check'; check.value = s.id;
      check.onchange = function() {{ updateShareSelectAll(); }};
      li.appendChild(check);
      var info = document.createElement('div'); info.className = 'app-info'; info.style.flex = '1';
      var tag = document.createElement('span'); tag.className = 'share-tag';
      if (s.resource_type === 'app') {{
        tag.classList.add('share-tag-app'); tag.textContent = '앱';
      }} else {{
        tag.classList.add('share-tag-data'); tag.textContent = '데이터';
      }}
      var nameSpan = document.createElement('span');
      nameSpan.textContent = ' ' + (s.resource_name || '') + ' → ' + (s.share_target || '');
      var meta = document.createElement('div'); meta.className = 'app-meta';
      meta.textContent = s.grant_type || '';
      info.appendChild(tag); info.appendChild(nameSpan); info.appendChild(meta);
      li.appendChild(info);
      list.appendChild(li);
    }});
  }}).catch(function() {{}});
}}

function toggleShareSelectAll(checked) {{
  document.querySelectorAll('#mySharesList .share-check').forEach(function(c) {{
    c.checked = checked;
  }});
}}

function updateShareSelectAll() {{
  var checks = document.querySelectorAll('#mySharesList .share-check');
  var allChecked = true;
  checks.forEach(function(c) {{ if (!c.checked) allChecked = false; }});
  document.getElementById('shareSelectAll').checked = allChecked;
}}

function bulkRevokeShares() {{
  var ids = [];
  document.querySelectorAll('#mySharesList .share-check:checked').forEach(function(c) {{
    ids.push(c.value);
  }});
  if (ids.length === 0) {{ alert('해제할 항목을 선택하세요.'); return; }}
  if (!confirm(ids.length + '개 공유를 해제합니다.')) return;
  apiFetch('/apps/bulk-revoke-shares', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{share_ids: ids}})
  }}).then(function() {{
    loadMyShares();
    loadMyApps();
    loadMyDatasets();
    var t = document.getElementById('hubToast');
    t.textContent = ids.length + '개 공유가 해제되었습니다.';
    t.style.display = 'block';
    setTimeout(function() {{ t.style.display = 'none'; }}, 2000);
  }}).catch(function() {{}});
}}

// ── 스킬 관리 ──
function loadMySkills() {{
  apiFetch('/skills/my').then(function(data) {{
    var skills = data.skills || [];
    var list = document.getElementById('mySkillsList');
    var count = document.getElementById('mySkillsCount');
    count.textContent = '(' + skills.length + ')';
    list.replaceChildren();
    if (skills.length === 0) {{
      var empty = document.createElement('li'); empty.className = 'empty-msg';
      empty.textContent = '등록된 스킬이 없습니다.';
      list.appendChild(empty); return;
    }}
    skills.forEach(function(s) {{
      var li = document.createElement('li'); li.className = 'skill-item';
      var info = document.createElement('div');
      var name = document.createElement('strong'); name.textContent = s.name || '';
      var meta = document.createElement('div'); meta.className = 'skill-meta';
      meta.textContent = (s.description || '') + (s.installs ? ' \u00b7 ' + s.installs + '회 설치' : '');
      info.appendChild(name); info.appendChild(meta);
      li.appendChild(info);
      var shareBtn = document.createElement('button'); shareBtn.className = 'btn-sm';
      shareBtn.style.borderColor = '#238636'; shareBtn.style.color = '#3fb950';
      shareBtn.textContent = '공유하기';
      shareBtn.onclick = function() {{ publishSkill(s.name); }};
      li.appendChild(shareBtn);
      list.appendChild(li);
    }});
  }}).catch(function() {{}});
}}

function loadSkillStore(sortBy) {{
  var query = '';
  if (sortBy === 'search') {{
    query = '?q=' + encodeURIComponent(document.getElementById('skillStoreSearch').value.trim());
  }} else {{
    query = '?sort=' + sortBy;
  }}
  apiFetch('/skills/store' + query).then(function(data) {{
    var skills = data.skills || [];
    var list = document.getElementById('skillStoreList');
    list.replaceChildren();
    if (skills.length === 0) {{
      var empty = document.createElement('li'); empty.className = 'empty-msg';
      empty.textContent = '스킬이 없습니다.';
      list.appendChild(empty); return;
    }}
    skills.forEach(function(s, idx) {{
      var li = document.createElement('li'); li.className = 'skill-item';
      var rank = document.createElement('span'); rank.className = 'skill-rank';
      rank.textContent = sortBy === 'popular' ? (idx + 1) : '';
      li.appendChild(rank);
      var info = document.createElement('div'); info.style.flex = '1';
      var name = document.createElement('strong'); name.textContent = s.name || '';
      var meta = document.createElement('div'); meta.className = 'skill-meta';
      meta.textContent = (s.author || '') + ' \u00b7 ' + (s.installs || 0) + '회 설치';
      if (s.description) {{
        var desc = document.createElement('div'); desc.className = 'skill-meta';
        desc.textContent = s.description;
        info.appendChild(name); info.appendChild(desc); info.appendChild(meta);
      }} else {{
        info.appendChild(name); info.appendChild(meta);
      }}
      li.appendChild(info);
      var installBtn = document.createElement('button'); installBtn.className = 'btn-sm';
      installBtn.style.borderColor = '#238636'; installBtn.style.color = '#3fb950';
      installBtn.textContent = '설치';
      installBtn.onclick = function() {{ installSkill(s.id); }};
      li.appendChild(installBtn);
      list.appendChild(li);
    }});
  }}).catch(function() {{}});
}}

function loadInstalledSkills() {{
  apiFetch('/skills/installed').then(function(data) {{
    var skills = data.skills || [];
    var list = document.getElementById('installedSkillsList');
    var count = document.getElementById('installedSkillsCount');
    count.textContent = '(' + skills.length + ')';
    list.replaceChildren();
    if (skills.length === 0) {{
      var empty = document.createElement('li'); empty.className = 'empty-msg';
      empty.textContent = '설치된 스킬이 없습니다.';
      list.appendChild(empty); return;
    }}
    skills.forEach(function(s) {{
      var li = document.createElement('li'); li.className = 'skill-item';
      var info = document.createElement('div'); info.style.flex = '1';
      var name = document.createElement('strong'); name.textContent = s.name || '';
      var meta = document.createElement('div'); meta.className = 'skill-meta';
      meta.textContent = (s.author || '') + ' \u00b7 ' + (s.version || '');
      info.appendChild(name); info.appendChild(meta);
      li.appendChild(info);
      var removeBtn = document.createElement('button'); removeBtn.className = 'btn-sm danger';
      removeBtn.textContent = '제거';
      removeBtn.onclick = function() {{ uninstallSkill(s.id); }};
      li.appendChild(removeBtn);
      list.appendChild(li);
    }});
  }}).catch(function() {{}});
}}

function installSkill(id) {{
  apiFetch('/skills/install', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{skill_id: id}})
  }}).then(function() {{
    loadInstalledSkills();
    loadSkillStore('popular');
    var t = document.getElementById('hubToast');
    t.textContent = '스킬이 설치되었습니다.';
    t.style.display = 'block';
    setTimeout(function() {{ t.style.display = 'none'; }}, 2000);
  }}).catch(function() {{}});
}}

function uninstallSkill(id) {{
  if (!confirm('스킬을 제거합니다.')) return;
  apiFetch('/skills/' + id, {{ method: 'DELETE' }}).then(function() {{
    loadInstalledSkills();
    var t = document.getElementById('hubToast');
    t.textContent = '스킬이 제거되었습니다.';
    t.style.display = 'block';
    setTimeout(function() {{ t.style.display = 'none'; }}, 2000);
  }}).catch(function() {{}});
}}

function publishSkill(name) {{
  if (!confirm(name + ' 스킬을 스토어에 공유합니다.')) return;
  apiFetch('/skills/publish', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{name: name}})
  }}).then(function() {{
    loadMySkills();
    var t = document.getElementById('hubToast');
    t.textContent = name + ' 스킬이 공유되었습니다.';
    t.style.display = 'block';
    setTimeout(function() {{ t.style.display = 'none'; }}, 2000);
  }}).catch(function() {{}});
}}

// 초기 로드 (앱 탭 — 기본 활성)
loadMyApps();
loadSharedApps();
loadAppStatus();
loadMyShares();
loadAnnouncements();

// 페이지 로드 시 기존 터미널 탭 확인
(function() {{
  if (localStorage.getItem('terminal_open') === 'true') {{
    try {{
      termWin = window.open('', 'claude-terminal-session');
      if (termWin && termWin.location && termWin.location.href !== 'about:blank') {{
        setCardState(true);
        startWatchingTerminal();
      }} else {{
        localStorage.removeItem('terminal_open');
      }}
    }} catch(e) {{
      localStorage.removeItem('terminal_open');
    }}
  }}
}})();
</script>
</body>
</html>"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Code Files — {title}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f1117; color: #e0e0e0; padding: 20px; }}
  h1 {{ font-size: 1.2rem; color: #8b949e; margin-bottom: 16px; }}
  h1 span {{ color: #58a6ff; }}

  /* Upload zone */
  .upload-zone {{
    border: 2px dashed #30363d; border-radius: 8px; padding: 24px;
    text-align: center; margin-bottom: 20px; transition: all 0.2s;
    cursor: pointer; position: relative;
  }}
  .upload-zone.dragover {{ border-color: #58a6ff; background: #161b22; }}
  .upload-zone p {{ color: #8b949e; margin: 4px 0; }}
  .upload-zone .icon {{ font-size: 2rem; margin-bottom: 8px; }}
  .upload-zone input {{ display: none; }}
  .upload-zone .btn {{
    display: inline-block; margin-top: 10px; padding: 8px 20px;
    background: #238636; color: #fff; border: none; border-radius: 6px;
    cursor: pointer; font-size: 0.9rem;
  }}
  .upload-zone .btn:hover {{ background: #2ea043; }}

  /* Progress */
  .progress {{ display: none; margin: 12px 0; }}
  .progress-bar {{
    height: 4px; background: #21262d; border-radius: 2px; overflow: hidden;
  }}
  .progress-fill {{
    height: 100%; background: #58a6ff; width: 0%; transition: width 0.3s;
  }}
  .progress-text {{ font-size: 0.8rem; color: #8b949e; margin-top: 4px; }}

  /* Toast */
  .toast {{
    position: fixed; top: 20px; right: 20px; padding: 12px 20px;
    border-radius: 6px; font-size: 0.9rem; display: none; z-index: 100;
  }}
  .toast.success {{ background: #238636; color: #fff; }}
  .toast.error {{ background: #da3633; color: #fff; }}

  /* File table */
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #21262d; }}
  th {{ color: #8b949e; font-weight: 500; font-size: 0.85rem; }}
  td a {{ color: #58a6ff; text-decoration: none; }}
  td a:hover {{ text-decoration: underline; }}
  td:nth-child(2) {{ color: #8b949e; font-size: 0.85rem; white-space: nowrap; }}
  td:last-child {{ white-space: nowrap; }}
  .del-btn {{
    padding: 3px 10px; font-size: 0.75rem; border: 1px solid #da3633;
    background: transparent; color: #da3633; border-radius: 4px;
    cursor: pointer; transition: all 0.15s;
  }}
  .del-btn:hover {{ background: #da3633; color: #fff; }}
</style>
</head>
<body>

<h1>Claude Code Files — <span>{title}</span></h1>

<div class="upload-zone" id="dropZone">
  <div class="icon">&#128228;</div>
  <p><strong>파일을 여기에 드래그하거나 클릭하여 업로드</strong></p>
  <p>최대 100MB / uploads 폴더에 저장됩니다</p>
  <input type="file" id="fileInput" multiple>
  <button class="btn" onclick="document.getElementById('fileInput').click()">파일 선택</button>
</div>

<div class="progress" id="progress">
  <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
  <div class="progress-text" id="progressText">업로드 중...</div>
</div>

<div class="toast" id="toast"></div>

<table>
  <thead><tr><th>Name</th><th>Size</th><th></th></tr></thead>
  <tbody>
    {parent}
    {entries}
  </tbody>
</table>

<script>
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const progress = document.getElementById('progress');
const progressFill = document.getElementById('progressFill');
const progressText = document.getElementById('progressText');
const toast = document.getElementById('toast');

function showToast(msg, type) {{
  toast.textContent = msg;
  toast.className = 'toast ' + type;
  toast.style.display = 'block';
  setTimeout(() => toast.style.display = 'none', 3000);
}}

function uploadFiles(files) {{
  if (!files.length) return;
  const formData = new FormData();
  for (const f of files) formData.append('files', f);

  const xhr = new XMLHttpRequest();
  progress.style.display = 'block';
  progressFill.style.width = '0%';

  xhr.upload.onprogress = (e) => {{
    if (e.lengthComputable) {{
      const pct = Math.round(e.loaded / e.total * 100);
      progressFill.style.width = pct + '%';
      progressText.textContent = pct + '% (' + files.length + '개 파일)';
    }}
  }};

  xhr.onload = () => {{
    progress.style.display = 'none';
    if (xhr.status === 200) {{
      const res = JSON.parse(xhr.responseText);
      showToast(res.count + '개 파일 업로드 완료', 'success');
      setTimeout(() => {{
        const base = window.location.pathname.match(/^\/files\/[^/]+/)?.[0] || '';
        window.location.href = base + '/uploads/';
      }}, 500);
    }} else {{
      showToast('업로드 실패: ' + xhr.statusText, 'error');
    }}
  }};

  xhr.onerror = () => {{
    progress.style.display = 'none';
    showToast('네트워크 오류', 'error');
  }};

  const basePath = window.location.pathname.match(/^\/files\/[^/]+/)?.[0] || '';
  xhr.open('POST', basePath + '/upload');
  xhr.send(formData);
}}

dropZone.addEventListener('dragover', (e) => {{
  e.preventDefault();
  dropZone.classList.add('dragover');
}});

dropZone.addEventListener('dragleave', () => {{
  dropZone.classList.remove('dragover');
}});

dropZone.addEventListener('drop', (e) => {{
  e.preventDefault();
  dropZone.classList.remove('dragover');
  uploadFiles(e.dataTransfer.files);
}});

fileInput.addEventListener('change', () => {{
  uploadFiles(fileInput.files);
  fileInput.value = '';
}});

function deleteFile(name) {{
  if (!confirm(name + ' 파일을 삭제하시겠습니까?')) return;
  const base = window.location.pathname.match(/^\/files\/[^/]+/)?.[0] || '';
  const xhr = new XMLHttpRequest();
  xhr.onload = () => {{
    if (xhr.status === 200) {{
      showToast('삭제 완료', 'success');
      setTimeout(() => location.reload(), 300);
    }} else {{
      showToast('삭제 실패', 'error');
    }}
  }};
  xhr.open('DELETE', base + '/delete?file=' + encodeURIComponent(name));
  xhr.send();
}}
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="File upload/download server")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--dir", default="/home/node/workspace")
    parser.add_argument("--bind", default="0.0.0.0")
    args = parser.parse_args()

    os.makedirs(os.path.join(args.dir, "uploads"), exist_ok=True)

    # functools.partial로 directory 인자를 전달해야 __init__에서 올바르게 설정됨
    handler = functools.partial(FileServerHandler, directory=args.dir)
    # Suppress default access logs (too noisy in container)
    FileServerHandler.log_message = lambda *a: None

    server = HTTPServer((args.bind, args.port), handler)
    print(f"File server started on {args.bind}:{args.port} (dir: {args.dir})")
    server.serve_forever()


if __name__ == "__main__":
    main()
