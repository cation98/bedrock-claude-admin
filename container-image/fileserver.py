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
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB


class FileServerHandler(SimpleHTTPRequestHandler):
    """파일 업로드를 지원하는 HTTP 핸들러."""

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
        """디렉토리 리스팅 시 업로드 UI 포함."""
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            self._send_directory_page(path)
            return
        super().do_GET()

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

        for name in items:
            fullpath = os.path.join(dirpath, name)
            display = html.escape(name)
            link = urllib.parse.quote(name)
            if os.path.isdir(fullpath):
                display += "/"
                link += "/"
                size = "-"
            else:
                size_bytes = os.path.getsize(fullpath)
                size = self._format_size(size_bytes)
            entries.append(f'<tr><td><a href="{link}">{display}</a></td><td>{size}</td></tr>')

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
  td:last-child {{ color: #8b949e; font-size: 0.85rem; white-space: nowrap; }}
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
  <thead><tr><th>Name</th><th>Size</th></tr></thead>
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
      setTimeout(() => location.reload(), 500);
    }} else {{
      showToast('업로드 실패: ' + xhr.statusText, 'error');
    }}
  }};

  xhr.onerror = () => {{
    progress.style.display = 'none';
    showToast('네트워크 오류', 'error');
  }};

  xhr.open('POST', '/upload');
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

    FileServerHandler.directory = args.dir
    # Suppress default access logs (too noisy in container)
    FileServerHandler.log_message = lambda *a: None

    server = HTTPServer((args.bind, args.port), FileServerHandler)
    print(f"File server started on {args.bind}:{args.port} (dir: {args.dir})")
    server.serve_forever()


if __name__ == "__main__":
    main()
