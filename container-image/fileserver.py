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

        # JSON 응답
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        import json
        self.wfile.write(json.dumps({
            "uploaded": uploaded,
            "count": len(uploaded),
            "directory": "uploads/",
        }).encode())

    def do_GET(self):
        """디렉토리 리스팅 시 업로드 UI 포함, /portal은 허브 페이지."""
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/portal" or parsed.path == "/portal/":
            self._send_portal_page()
            return
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            self._send_directory_page(path)
            return
        super().do_GET()

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
  .footer {{ text-align: center; margin-top: 32px; color: #484f58; font-size: 0.75rem; }}
</style>
</head>
<body>

<div class="container">
  <div class="header">
    <h1>Claude Code <span class="accent">Terminal</span></h1>
    <p>{user_name} ({user_id}) &middot; {pod_name}</p>
    <a href="/" class="logout-btn" onclick="localStorage.clear();">로그아웃</a>
  </div>

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

  <div class="guide">
    <h3>터미널에서 사용 가능한 명령어</h3>
    <div class="guide-grid">
      <div class="guide-item"><code>claude</code> <span class="label">Claude Code 시작</span></div>
      <div class="guide-item"><code>psql-safety</code> <span class="label">안전관리 DB</span></div>
      <div class="guide-item"><code>psql-tango</code> <span class="label">TANGO 알람 DB</span></div>
      <div class="guide-item"><code>/report</code> <span class="label">보고서 생성</span></div>
      <div class="guide-item"><code>/excel</code> <span class="label">엑셀 파일 생성</span></div>
      <div class="guide-item"><code>ls ~/workspace/uploads</code> <span class="label">업로드된 파일 확인</span></div>
    </div>
  </div>

  <div class="footer">Claude Code Platform &middot; Powered by AWS Bedrock</div>
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
