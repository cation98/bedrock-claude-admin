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
import argparse
import urllib.parse
import cgi
import functools
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

        import json
        try:
            filepath.unlink()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"deleted": safe_name}).encode())
        except OSError as e:
            self.send_error(500, str(e))

    def do_POST(self):
        """파일 업로드 처리 (POST /upload)."""
        parsed = urllib.parse.urlparse(self.path)
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
                import shutil
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
        import json
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
        if parsed.path == "/portal" or parsed.path == "/portal/":
            self._send_portal_page()
            return
        if parsed.path.startswith("/api/browse"):
            self._send_file_listing_json(parsed)
            return
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            self._send_directory_page(path)
            return
        super().do_GET()

    def _send_file_listing_json(self, parsed):
        """파일 브라우저용 JSON API — /api/browse?path=uploads"""
        import json as _json
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
                if os.path.isdir(full):
                    entries.append({"name": name, "path": entry_path, "type": "dir", "size": 0})
                else:
                    size = os.path.getsize(full)
                    entries.append({"name": name, "path": entry_path, "type": "file", "size": size})
        except OSError:
            pass

        self._send_json(200, {"path": rel_path, "entries": entries})

    def _send_json(self, status, data):
        import json as _json
        body = _json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
            import subprocess
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
            import subprocess
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
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0d1117; color: #e6edf3; min-height: 100vh;
         display: flex; flex-direction: column; align-items: center; justify-content: center; }}

  .container {{ max-width: 720px; width: 90%; padding: 40px 0; }}

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

  .footer {{ text-align: center; margin-top: 32px; color: #484f58; font-size: 0.75rem; }}
</style>
</head>
<body>

<div class="container">
  <div class="header">
    <h1>Claude Code <span class="accent">Terminal</span></h1>
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

  <div class="cards">
    <a class="card" href="/terminal/{pod_name}/" id="terminalCard" onclick="return openTerminal(event, this)">
      <div class="icon" id="terminalIcon">&#9000;</div>
      <h2 id="terminalTitle">터미널 접속</h2>
      <p id="terminalDesc">Claude Code AI 코딩 어시스턴트<br>웹 터미널에서 바로 실행</p>
      <span class="badge badge-blue" id="terminalBadge">탭에서 열기</span>
    </a>
    <a class="card" href="/files/{pod_name}/" target="_blank">
      <div class="icon">&#128228;</div>
      <h2>파일 관리</h2>
      <p>파일 업로드 (드래그&amp;드롭)<br>결과물 다운로드</p>
      <span class="badge badge-green">새 탭에서 열기</span>
    </a>
    <a class="card" href="/app/{pod_name}/" target="_blank">
      <div class="icon">&#127760;</div>
      <h2>웹앱</h2>
      <p>터미널에서 만든 대시보드<br>웹앱 접속 (포트 3000)</p>
      <span class="badge badge-green">새 탭에서 열기</span>
    </a>
  </div>

  <!-- 탭 바 -->
  <div class="hub-tabs">
    <button class="hub-tab active" onclick="switchHubTab('manage')">앱/데이터 관리</button>
    <button class="hub-tab" onclick="switchHubTab('guide')">명령어 가이드</button>
  </div>

  <!-- 탭 1: 앱/데이터 관리 -->
  <div class="hub-tab-content active" id="tab-manage">

  <!-- 나의 배포 앱 -->
  <div class="app-section" id="myAppsSection">
    <h3>나의 배포 앱 <span class="count" id="myAppsCount">(0)</span></h3>
    <ul class="app-list" id="myAppsList">
      <li class="empty-msg">배포된 앱이 없습니다. 터미널에서 <code>deploy my-app</code>으로 배포하세요.</li>
    </ul>
  </div>

  <!-- 나에게 공유된 앱 -->
  <div class="app-section" id="sharedAppsSection">
    <h3>공유 받은 앱 <span class="count" id="sharedAppsCount">(0)</span></h3>
    <ul class="app-list" id="sharedAppsList">
      <li class="empty-msg">공유 받은 앱이 없습니다.</li>
    </ul>
  </div>

  <!-- 내 공유 데이터 -->
  <div class="app-section" id="myDatasetsSection">
    <h3 style="display:flex;justify-content:space-between;align-items:center;">
      <span>내 공유 데이터 <span class="count" id="myDatasetsCount">(0)</span></span>
      <button class="btn-sm" style="border-color:#238636;color:#3fb950;padding:5px 12px;font-size:0.78rem;"
              onclick="toggleRegisterForm()">+ 데이터셋 등록</button>
    </h3>
    <!-- 데이터셋 등록 폼 -->
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

  <div class="guide" style="margin-top:16px;">
    <div style="font-size:0.82rem;color:#8b949e;line-height:1.8;">
      <p><strong style="color:#e6edf3;">데이터 공유</strong>: 10MB \ucd08\uacfc Excel/CSV \u2192 SQLite \uc790\ub3d9 \ubcc0\ud658 \u2192 \uc704 "\ub0b4 \uacf5\uc720 \ub370\uc774\ud130"\uc5d0\uc11c \uac1c\uc778/\uc870\uc9c1 \uc9c0\uc815</p>
      <p><strong style="color:#e6edf3;">\uc6f9\uc571 \uacf5\uc720</strong>: <code style="background:#21262d;padding:2px 6px;border-radius:4px;font-size:0.78rem;color:#79c0ff;">deploy my-app --acl "N1001063"</code> \ub610\ub294 \uc704 "\ub098\uc758 \ubc30\ud3ec \uc571"\uc5d0\uc11c \uc811\uadfc \uad00\ub9ac</p>
      <p style="margin-top:4px;color:#58a6ff;">\uacf5\uc720 \ubcc0\uacbd\uc740 60\ucd08 \uc774\ub0b4 \uc790\ub3d9 \ubc18\uc601\ub429\ub2c8\ub2e4.</p>
    </div>
  </div>

  </div><!-- /tab-manage -->

  <!-- 탭 2: 명령어 가이드 -->
  <div class="hub-tab-content" id="tab-guide">

  <div class="guide">
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
    <div class="modal" style="position:relative;">
      <button class="close-btn" onclick="closeAclModal()">&times;</button>
      <h3>접근 권한 관리 — <span id="aclAppName"></span></h3>
      <div class="search-box">
        <input type="text" id="aclSearchInput" placeholder="사번 또는 이름 검색..." onkeypress="if(event.key==='Enter')searchUsers()">
        <button class="search-btn" onclick="searchUsers()">검색</button>
      </div>
      <div id="searchResults"></div>
      <h4 style="font-size:0.85rem;color:#8b949e;margin:12px 0 8px;">현재 허용된 사용자</h4>
      <ul class="acl-list" id="aclUserList"></ul>
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
  apiFetch('/apps/my').then(function(data) {{
    var apps = data.apps || [];
    var section = document.getElementById('myAppsSection');
    var list = document.getElementById('myAppsList');
    var count = document.getElementById('myAppsCount');
    if (apps.length === 0) {{ return; }}
    section.style.display = 'block';
    count.textContent = '(' + apps.length + ')';
    list.replaceChildren();
    apps.forEach(function(a) {{ list.appendChild(buildAppItem(a, true)); }});
  }}).catch(function() {{}});
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

var currentAclApp = '';
function openAclModal(appName) {{
  currentAclApp = appName;
  document.getElementById('aclAppName').textContent = appName;
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
  info.textContent = (u.name || u.username) + ' (' + u.username + ') ';
  var team = document.createElement('span'); team.className = 'team';
  team.textContent = u.team_name || '';
  info.appendChild(team); li.appendChild(info);
  if (canRevoke) {{
    var btn = document.createElement('button'); btn.className = 'btn-sm danger';
    btn.textContent = '\ud68c\uc218';
    btn.onclick = function() {{ revokeAccess(u.username); }};
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
    body: JSON.stringify({{username: username}})
  }}).then(function() {{
    loadAclUsers(currentAclApp);
    document.getElementById('searchResults').replaceChildren();
    document.getElementById('aclSearchInput').value = '';
  }});
}}

function revokeAccess(username) {{
  if (!confirm(username + ' \uc758 \uc811\uadfc \uad8c\ud55c\uc744 \ud68c\uc218\ud569\ub2c8\ub2e4.')) return;
  apiFetch('/apps/' + currentAclApp + '/acl/' + username, {{ method: 'DELETE' }})
    .then(function() {{ loadAclUsers(currentAclApp); }});
}}

function undeployApp(appName) {{
  if (!confirm(appName + ' \uc571\uc744 \uc0ad\uc81c\ud569\ub2c8\ub2e4. \uc774 \uc791\uc5c5\uc740 \ub418\ub3cc\ub9b4 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.')) return;
  apiFetch('/apps/' + appName, {{ method: 'DELETE' }})
    .then(function() {{ loadMyApps(); }});
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
  var filesUrl = '/files/{pod_name}/';
  fetch(filesUrl.replace(/\/$/, '') + '/api/browse?path=' + encodeURIComponent(dirPath))
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

// ── 탭 전환 ──
function switchHubTab(tabName) {{
  document.querySelectorAll('.hub-tab').forEach(function(t) {{ t.classList.remove('active'); }});
  document.querySelectorAll('.hub-tab-content').forEach(function(c) {{ c.classList.remove('active'); }});
  document.getElementById('tab-' + tabName).classList.add('active');
  // 해당 버튼 활성화
  document.querySelectorAll('.hub-tab').forEach(function(t) {{
    if (t.textContent.indexOf(tabName === 'manage' ? '\uc571' : '\uba85\ub839') >= 0) t.classList.add('active');
  }});
}}
// 탭 버튼 클릭 이벤트 (onclick 대신 안전한 방식)
document.querySelectorAll('.hub-tab').forEach(function(btn, idx) {{
  btn.addEventListener('click', function() {{
    document.querySelectorAll('.hub-tab').forEach(function(t) {{ t.classList.remove('active'); }});
    document.querySelectorAll('.hub-tab-content').forEach(function(c) {{ c.classList.remove('active'); }});
    btn.classList.add('active');
    var tabs = document.querySelectorAll('.hub-tab-content');
    if (tabs[idx]) tabs[idx].classList.add('active');
  }});
}});

// 초기 로드
loadMyApps();
loadSharedApps();
loadMyDatasets();
loadSharedDatasets();

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
