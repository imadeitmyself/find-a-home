"""HTTP server: a phone-friendly form to add magnets, plus file downloads."""

from __future__ import annotations

import hmac
import html
import logging
import posixpath
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .config import Config
from .downloader import Downloader, human_size

logger = logging.getLogger("magnet_grab")

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>magnet grab</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0; padding: 1.5rem;
          max-width: 640px; margin: 0 auto; }}
  h1 {{ font-size: 1.3rem; }}
  textarea {{ width: 100%; box-sizing: border-box; font-size: 1rem; padding: .6rem;
              min-height: 5rem; border-radius: .5rem; }}
  button {{ font-size: 1.1rem; padding: .7rem 1.2rem; margin-top: .8rem; width: 100%;
            border: 0; border-radius: .5rem; background: #2b6cb0; color: #fff; }}
  .msg {{ padding: .8rem; border-radius: .5rem; margin-bottom: 1rem; background: #2f855a22; }}
  .err {{ background: #c53a3a22; }}
  ul {{ padding-left: 1.1rem; }}
  li {{ margin: .25rem 0; }}
  .meta {{ color: #888; font-size: .85rem; }}
  a {{ word-break: break-all; }}
</style>
</head>
<body>
<h1>🧲 magnet grab</h1>
{message}
<form method="post" action="/add{token_qs}">
  <textarea name="magnet" placeholder="magnet:?xt=urn:btih:..." autofocus></textarea>
  <button type="submit">Download on the VPS</button>
</form>
{jobs}
</body>
</html>
"""


def render_page(config: Config, downloader: Downloader, message_html: str = "") -> str:
    token_qs = "?token=%s" % quote(config.access_token) if config.access_token else ""
    jobs_html = _render_jobs(config, downloader)
    return _PAGE.format(message=message_html, token_qs=token_qs, jobs=jobs_html)


def _render_jobs(config: Config, downloader: Downloader) -> str:
    jobs = downloader.jobs()
    if not jobs:
        return ""
    rows = ["<h2>Recent</h2>", "<ul>"]
    token_qs = "?token=%s" % quote(config.access_token) if config.access_token else ""
    for job in jobs[:15]:
        status = html.escape(job.status)
        name = html.escape(job.name)
        if job.status == "done":
            detail = "%d file%s · %s" % (
                len(job.files),
                "" if len(job.files) == 1 else "s",
                human_size(job.total_size),
            )
        elif job.status == "error":
            detail = html.escape(job.error)
        else:
            detail = status
        link = "/files/%s/%s" % (job.infohash, token_qs)
        rows.append(
            '<li><a href="%s">%s</a> <span class="meta">[%s] %s</span></li>'
            % (html.escape(link), name, status, html.escape(detail))
        )
    rows.append("</ul>")
    return "\n".join(rows)


def make_handler(config: Config, downloader: Downloader):
    download_root = config.download_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "magnet-grab/0.1"

        def log_message(self, fmt, *args):  # route through logging
            logger.info("%s - %s", self.address_string(), fmt % args)

        # -- auth -----------------------------------------------------------

        def _authorized(self, query: dict) -> bool:
            if not config.access_token:
                return True
            provided = (query.get("token", [""])[0]) or self.headers.get("X-Access-Token", "")
            return hmac.compare_digest(provided, config.access_token)

        def _deny(self):
            self.send_error(HTTPStatus.UNAUTHORIZED, "Missing or invalid token")

        # -- dispatch -------------------------------------------------------

        def do_GET(self):  # noqa: N802
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            if not self._authorized(query):
                return self._deny()
            path = unquote(parsed.path)

            if path == "/" or path == "":
                return self._send_html(render_page(config, downloader))
            if path == "/add":
                magnet = query.get("magnet", [""])[0]
                return self._handle_add(magnet)
            if path.startswith("/files/"):
                return self._serve_file(path[len("/files/"):])
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self):  # noqa: N802
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            if not self._authorized(query):
                return self._deny()
            if unquote(parsed.path) != "/add":
                return self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
            magnet = parse_qs(body).get("magnet", [""])[0].strip()
            self._handle_add(magnet)

        # -- handlers -------------------------------------------------------

        def _handle_add(self, magnet: str):
            magnet = (magnet or "").strip()
            if not magnet:
                msg = '<div class="msg err">Paste a magnet link first.</div>'
                return self._send_html(render_page(config, downloader, msg), HTTPStatus.BAD_REQUEST)
            try:
                job = downloader.submit(magnet)
            except ValueError as exc:
                msg = '<div class="msg err">%s</div>' % html.escape(str(exc))
                return self._send_html(render_page(config, downloader, msg), HTTPStatus.BAD_REQUEST)
            msg = (
                '<div class="msg">Started: <b>%s</b>. '
                "You'll get a Telegram message when it's ready.</div>"
                % html.escape(job.name)
            )
            self._send_html(render_page(config, downloader, msg))

        def _serve_file(self, rel: str):
            safe = _safe_join(download_root, rel)
            if safe is None:
                return self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            if safe.is_dir():
                return self._send_html(_render_dir_listing(rel, safe, config.access_token))
            if not safe.is_file():
                return self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            self._send_file_with_ranges(safe)

        # -- low-level senders ---------------------------------------------

        def _send_html(self, text: str, status: HTTPStatus = HTTPStatus.OK):
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _send_file_with_ranges(self, path: Path):
            file_size = path.stat().st_size
            ctype = _guess_type(path)
            start, end = 0, file_size - 1
            status = HTTPStatus.OK
            range_header = self.headers.get("Range")
            if range_header and range_header.startswith("bytes="):
                rng = _parse_range(range_header, file_size)
                if rng is None:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", "bytes */%d" % file_size)
                    self.end_headers()
                    return
                start, end = rng
                status = HTTPStatus.PARTIAL_CONTENT

            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, file_size))
            self.send_header(
                "Content-Disposition", 'attachment; filename="%s"' % path.name.replace('"', "")
            )
            self.end_headers()
            if self.command == "HEAD":
                return
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    remaining -= len(chunk)

    return Handler


def _safe_join(root: Path, rel: str):
    rel = rel.strip("/")
    normalized = posixpath.normpath(rel) if rel else ""
    if normalized in ("", "."):
        return root
    if normalized.startswith("..") or normalized.startswith("/"):
        return None
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _render_dir_listing(rel: str, directory: Path, token: str) -> str:
    token_qs = "?token=%s" % quote(token) if token else ""
    rel = rel.strip("/")
    items = []
    for child in sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        name = child.name + ("/" if child.is_dir() else "")
        href = "/files/" + ("%s/" % rel if rel else "") + quote(child.name)
        if child.is_dir():
            href += "/"
        href += token_qs
        meta = "" if child.is_dir() else " <span style='color:#888'>(%s)</span>" % human_size(
            child.stat().st_size
        )
        items.append('<li><a href="%s">%s</a>%s</li>' % (html.escape(href), html.escape(name), meta))
    body = "\n".join(items) or "<li>(empty)</li>"
    title = html.escape(rel or "downloads")
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>%s</title><h1>%s</h1><ul>%s</ul>" % (title, title, body)
    )


def _guess_type(path: Path) -> str:
    import mimetypes

    ctype, _ = mimetypes.guess_type(str(path))
    return ctype or "application/octet-stream"


def _parse_range(header: str, file_size: int):
    spec = header[len("bytes="):].split(",")[0].strip()
    if "-" not in spec:
        return None
    start_s, end_s = spec.split("-", 1)
    try:
        if not start_s:  # suffix range: bytes=-N (last N bytes)
            length = int(end_s)
            if length <= 0:
                return None
            start = max(0, file_size - length)
            return start, file_size - 1
        start = int(start_s)
        end = int(end_s) if end_s else file_size - 1
    except ValueError:
        return None
    end = min(end, file_size - 1)
    if start > end or start >= file_size:
        return None
    return start, end


def serve(config: Config, downloader: Downloader) -> None:
    config.download_dir.mkdir(parents=True, exist_ok=True)
    handler = make_handler(config, downloader)
    httpd = ThreadingHTTPServer((config.host, config.port), handler)
    logger.info(
        "magnet-grab listening on %s:%d (public: %s, downloads: %s)",
        config.host,
        config.port,
        config.public_url,
        config.download_dir,
    )
    if not config.access_token:
        logger.warning("MAGNET_GRAB_TOKEN is not set — anyone who reaches this port can use it.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
