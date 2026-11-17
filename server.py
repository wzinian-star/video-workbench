from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import signal
import sys
import tempfile
import threading
import uuid
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import workflows

CATEGORIES = {"自制", "收藏参考", "待整理"}
MEDIA = {
    ".mp4": ("video", "video/mp4"), ".m4v": ("video", "video/mp4"),
    ".mov": ("video", "video/quicktime"), ".webm": ("video", "video/webm"),
    ".png": ("image", "image/png"), ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"), ".gif": ("image", "image/gif"),
    ".webp": ("image", "image/webp"), ".avif": ("image", "image/avif"),
    ".mp3": ("audio", "audio/mpeg"), ".m4a": ("audio", "audio/mp4"),
    ".wav": ("audio", "audio/wav"), ".aac": ("audio", "audio/aac"),
    ".flac": ("audio", "audio/flac"), ".ogg": ("audio", "audio/ogg"),
}
MAX_UPLOAD = 128 * 1024 * 1024
MAX_JSON = 512 * 1024
IDENT = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}", re.ASCII)
ASSET_PATH = re.compile(r"/media/asset/([^/]+)")
JOB_PATH = re.compile(r"/media/job/([^/]+)/(sample|final)")
AUDIO_MEDIA_PATH = re.compile(r"/media/audio/([^/]+)/([^/]+)")
AUDIO_ANALYZE_PATH = re.compile(r"/api/assets/([^/]+)/audio")
CARDS_PATH = re.compile(r"/api/assets/([^/]+)/cards")
APPROVE_PATH = re.compile(r"/api/jobs/([^/]+)/approve")
STATIC = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"),
          "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
CATALOG_LOCK = threading.RLock()
try:
    import jobs
except ModuleNotFoundError as exc:
    if exc.name != "jobs":
        raise
    jobs = None
try:
    import audio_refs
except ModuleNotFoundError as exc:
    if exc.name != "audio_refs":
        raise
    audio_refs = None


class RequestError(ValueError):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def read_catalog(root: Path) -> list[dict]:
    p = root / "catalog.json"
    if not p.exists():
        return []
    with CATALOG_LOCK:
        cards = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(cards, list) or any(not isinstance(x, dict) for x in cards):
        raise RequestError("素材索引格式不正确。", 500)
    return cards


def save_catalog(root: Path, items: list[dict]):
    atomic_bytes(root / "catalog.json", (json.dumps(items, ensure_ascii=False, indent=2) + "\n").encode())


def atomic_bytes(path: Path, content: bytes):
    fd, temp = tempfile.mkstemp(prefix=".wb-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def resolve_media(root: Path, relative, directory: str):
    if not isinstance(relative, (str, Path)) or not relative:
        return None
    dest = (root / relative).resolve()
    if not dest.is_relative_to(root / directory) or not dest.is_file():
        return None
    return dest if dest.suffix.lower() in MEDIA else None


def asset_response(root: Path, item: dict):
    out = {**item}
    media = resolve_media(root, item.get("file"), "素材")
    key = item.get("id", "")
    out["media"] = f"/media/asset/{key}" if media and IDENT.fullmatch(key) else None
    out.setdefault("cards", [])
    return out


def clean_field(body: dict, key: str, n: int, default="", required=False):
    val = body.get(key, default)
    if not isinstance(val, str):
        raise RequestError(f"字段 {key} 应为文本。")
    val = val.strip()
    if len(val) > n or (required and not val):
        raise RequestError(f"请检查字段 {key}（最多 {n} 字）。")
    return val


def valid_url(value: str):
    if any(ch.isspace() or ord(ch) < 32 for ch in value):
        return False
    try:
        u = urlsplit(value)
        return u.scheme in ("http", "https") and bool(u.hostname) and not (u.username or u.password or "@" in u.netloc)
    except ValueError:
        return False


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, root, port=8765):
        self.root = Path(root).resolve()
        self.token = secrets.token_urlsafe(24)
        super().__init__(("127.0.0.1", port), WorkbenchHandler)
        self.allowed_hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}


class WorkbenchHandler(BaseHTTPRequestHandler):
    # Hide access logs, incl. filenames, links and titles.
    def log_message(self, *args):
        return

    def do_GET(self):
        self.dispatch(head=False, post=False)

    def do_HEAD(self):
        self.dispatch(head=True, post=False)

    def do_POST(self):
        self.dispatch(head=False, post=True)

    @property
    def root(self) -> Path:
        return self.server.root

    def dispatch(self, *, head, post):
        try:
            self.check_origin(post)
            path = urlsplit(self.path).path
            if post:
                self.mutate(path)
            else:
                self.get(path, head)
        except RequestError as exc:
            self.respond({"error": str(exc)}, exc.status, head)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            self.respond({"error": "本地工作台操作失败，请检查素材索引和文件。"}, 500, head)

    def check_origin(self, post):
        host = self.headers.get("Host", "").lower()
        origin = self.headers.get("Origin")
        site = self.headers.get("Sec-Fetch-Site")
        if host not in self.server.allowed_hosts or (origin is not None and origin.lower() != "http://" + host) or site == "cross-site":
            raise RequestError("工作台仅允许本机同源访问。", 403)
        if post:
            supplied = self.headers.get("X-Workbench-Token", "").encode("utf-8")
            if not hmac.compare_digest(supplied, self.server.token.encode()):
                raise RequestError("连接已过期；请刷新工作台。", 403)

    def headers_common(self, status, content_type, n, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(n))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def respond(self, obj, status=200, head=False):
        encoded = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.headers_common(status, "application/json; charset=utf-8", len(encoded))
        if not head:
            self.wfile.write(encoded)

    def get(self, path, head):
        if path in STATIC:
            fname, typ = STATIC[path]
            file = (self.root / "ui" / fname).resolve()
            if not file.is_relative_to(self.root / "ui") or not file.is_file():
                raise RequestError("工作台页面文件还未就绪。", 404)
            self.send_file(file, typ + "; charset=utf-8", head, ranged=False)
        elif path == "/api/state":
            assets = [asset_response(self.root, item) for item in read_catalog(self.root)]
            skills = self.run_jobs(workflows.list_workflows, self.root)
            existing = jobs.list_jobs(self.root) if jobs else []
            self.respond({"token":self.server.token,"assets":assets,"skills":skills,"jobs":existing}, head=head)
        elif result := ASSET_PATH.fullmatch(path):
            key = result[1]
            item = self.find_asset(key)
            file = resolve_media(self.root, item.get("file"), "素材")
            if not file:
                raise RequestError("该素材没有可在本地预览的文件。", 404)
            self.send_file(file, MEDIA[file.suffix.lower()][1], head)
        elif result := AUDIO_MEDIA_PATH.fullmatch(path):
            if audio_refs is None:
                raise RequestError("音频工具还未就绪。",503)
            key, clip_id = result.groups()
            self.check_id(key)
            self.check_id(clip_id)
            try:
                path = audio_refs.reference_clip_path(self.root, key, clip_id)
            except (ValueError, FileNotFoundError):
                path = None
            file = resolve_media(self.root, path, ".reference_audio")
            if not file or MEDIA[file.suffix.lower()][0] != "audio":
                raise RequestError("试听音频不存在。", 404)
            self.send_file(file, MEDIA[file.suffix.lower()][1], head)
        elif result := JOB_PATH.fullmatch(path):
            self.ensure_jobs()
            key, phase = result.groups()
            self.check_id(key)
            path = self.run_jobs(jobs.media_path, self.root, key, phase)
            file = resolve_media(self.root, path, "任务")
            if not file:
                raise RequestError("视频产物尚未生成。", 404)
            self.send_file(file, MEDIA[file.suffix.lower()][1], head)
        else:
            raise RequestError("页面不存在。", 404)

    def send_file(self, file, content_type, head, ranged=True):
        with file.open("rb") as stream:
            n = os.fstat(stream.fileno()).st_size
            start, end, status = 0, n - 1, 200
            extra = {"Accept-Ranges":"bytes"} if ranged else {}
            header = self.headers.get("Range") if ranged else None
            if header:
                match = re.fullmatch(r"bytes=(\d{0,20})-(\d{0,20})", header.strip())
                if not match or (not match[1] and not match[2]) or not n:
                    return self.headers_common(416, content_type, 0, {**extra, "Content-Range":f"bytes */{n}"})
                left, right = match.groups()
                if left:
                    start = int(left)
                    end = min(int(right), n-1) if right else n-1
                else:
                    start = n - min(int(right), n)
                if start > end or start >= n:
                    return self.headers_common(416, content_type, 0, {**extra, "Content-Range":f"bytes */{n}"})
                status = 206
                extra["Content-Range"] = f"bytes {start}-{end}/{n}"
            count = end - start + 1
            self.headers_common(status, content_type, count, extra)
            if head:
                return
            stream.seek(start)
            remaining = count
            while remaining > 0:
                part = stream.read(min(remaining, 128 * 1024))
                if not part:
                    break
                self.wfile.write(part)
                remaining -= len(part)

    def read_body(self, maximum):
        if self.headers.get("Transfer-Encoding"):
            raise RequestError("不支持分块请求。")
        length = self.headers.get("Content-Length", "0")
        if not re.fullmatch("[0-9]{1,12}", length):
            raise RequestError("请求长度无效。")
        n = int(length)
        if n > maximum:
            raise RequestError("文件过大（单条素材上限 128 MB）。", 413)
        body = self.rfile.read(n)
        if len(body) != n:
            raise RequestError("未收到完整请求。")
        return body

    def json_body(self):
        if self.headers.get_content_type() != "application/json":
            raise RequestError("请求格式应为 JSON。", 415)
        try:
            val = json.loads(self.read_body(MAX_JSON))
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            raise RequestError("无法读取 JSON 请求。") from None
        if not isinstance(val, dict):
            raise RequestError("请求须为 JSON 对象。")
        return val

    def multipart_body(self):
        header = self.headers.get("Content-Type", "")
        try:
            prefix = ("Content-Type: " + header + "\r\nMIME-Version: 1.0\r\n\r\n").encode("ascii")
        except UnicodeError:
            raise RequestError("上传格式无效。") from None
        envelope = BytesParser(policy=policy.default).parsebytes(prefix + self.read_body(MAX_UPLOAD))
        if not envelope.is_multipart() or envelope.defects:
            raise RequestError("上传格式无效。")
        fields, blob, filename = {}, None, None
        for part in envelope.iter_parts():
            if part.is_multipart() or part.get_content_disposition() != "form-data":
                raise RequestError("上传格式无效。")
            key = part.get_param("name", header="Content-Disposition")
            value = part.get_payload(decode=True)
            if not isinstance(value, bytes):
                continue
            if key == "file":
                if blob is not None:
                    raise RequestError("每次只可以导入一个文件。")
                blob, filename = value, part.get_filename()
            elif key in ("title","category","notes"):
                if len(value) > 40_000:
                    raise RequestError("素材说明太长。")
                try:
                    fields[key] = value.decode("utf-8")
                except UnicodeError:
                    raise RequestError("上传字段不是有效文本。") from None
        if not blob or not filename:
            raise RequestError("请选一个素材文件。")
        return fields, blob, filename

    def check_id(self, key):
        if not isinstance(key, str) or not IDENT.fullmatch(key):
            raise RequestError("记录不存在。", 404)

    def find_asset(self, key, catalog=None):
        self.check_id(key)
        return next((it for it in (read_catalog(self.root) if catalog is None else catalog) if it.get("id") == key), None) or self.raise_missing()

    @staticmethod
    def raise_missing():
        raise RequestError("素材不存在。", 404)

    def ensure_jobs(self):
        if jobs is None:
            raise RequestError("制作引擎尚未就绪。", 503)

    def run_jobs(self, operation, *args):
        try:
            return operation(*args)
        except ValueError as exc:
            raise RequestError(str(exc), getattr(exc,"status",400)) from None
        except FileNotFoundError:
            raise RequestError("指定的任务或产物不存在。", 404) from None

    def mutate(self, path):
        if path == "/api/assets":
            return self.add_asset()
        if path == "/api/workflows":
            return self.respond(self.run_jobs(workflows.create_workflow, self.root, self.json_body()), 201)
        if m := AUDIO_ANALYZE_PATH.fullmatch(path):
            return self.analyze_audio(m[1])
        if m := CARDS_PATH.fullmatch(path):
            return self.add_card(m[1])
        if path == "/api/jobs":
            self.ensure_jobs()
            inp = self.json_body()
            if inp.get("mode", "sample") not in ("sample", "storyboard"):
                raise RequestError("请先制作并审阅样片，再启动全片。")
            return self.respond(self.run_jobs(jobs.start_job, self.root, inp), 201)
        if m := APPROVE_PATH.fullmatch(path):
            self.ensure_jobs()
            key = m[1]
            self.check_id(key)
            return self.respond(self.run_jobs(jobs.approve_job, self.root, key), 200)
        raise RequestError("路径不存在。", 404)

    def add_asset(self):
        blob = filename = None
        mode = self.headers.get_content_type()
        if mode == "multipart/form-data":
            body, blob, filename = self.multipart_body()
        elif mode == "application/json":
            body = self.json_body()
        else:
            raise RequestError("请选择本地文件或填写网页链接。", 415)
        name_hint = (filename or "").replace("\\", "/").rsplit("/",1)[-1]
        title = clean_field(body, "title", 180) or name_hint
        if not title or len(title) > 180:
            raise RequestError("请填写素材名称（最多 180 字）。")
        key = uuid.uuid4().hex
        category = clean_field(body,"category",20,"待整理")
        if category not in CATEGORIES:
            raise RequestError("素材分类不存在。")
        notes = clean_field(body,"notes",8000)
        relative = url = None
        kind = "link"
        if blob is not None:
            ext = Path(name_hint).suffix.lower()
            if ext not in MEDIA:
                raise RequestError("不支持该文件类型；可导入常见视频、图片或音频。",415)
            kind = MEDIA[ext][0]
            folder = self.root / "素材" / category
            if folder.resolve() != folder:
                raise RequestError("素材目录不能是外部链接。", 500)
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / (key + ext)
            relative = str(target.relative_to(self.root))
        else:
            url = clean_field(body,"url",3000,required=True)
            if not valid_url(url):
                raise RequestError("仅支持不含登录凭据的 HTTP 或 HTTPS 链接。")
        item = {"id":key,"title":title,"kind":kind,"category":category,
                "file":relative,"url":url,"notes":notes,
                "dimensions":[],"review":"待分析" if url else "待整理","cards":[]}
        with CATALOG_LOCK:
            records = read_catalog(self.root)
            if blob is not None:
                atomic_bytes(target, blob)
            try:
                save_catalog(self.root, [*records, item])
            except Exception:
                if blob is not None:
                    target.unlink(missing_ok=True)
                raise
        self.respond(asset_response(self.root, item), 201)

    def analyze_audio(self, key):
        item = self.find_asset(key)
        file = resolve_media(self.root,item.get("file"),"素材")
        if not file or MEDIA[file.suffix.lower()][0] not in ("audio","video"):
            raise RequestError("这条素材没有可提取的本地音频。请导入有权使用的本地视频或独立音频；工作台不会下载链接。")
        if audio_refs is None:
            raise RequestError("音频工具还未就绪。",503)
        body = self.json_body()
        segment = clean_field(body, "segment", 100)
        language = clean_field(body, "language", 10, "auto")
        try:
            result = audio_refs.analyze_reference(self.root,key,file,segment or None,language)
        except ValueError as exc:
            raise RequestError(str(exc)) from None
        clip_id = result.get("clip_id")
        if not isinstance(clip_id,str) or not IDENT.fullmatch(clip_id):
            raise RequestError("未生成可试听的音频。",500)
        self.respond({**result,"audio_url":f"/media/audio/{key}/{clip_id}"})

    def add_card(self, key):
        body = self.json_body()
        card = {
            "id":uuid.uuid4().hex,
            "segment":clean_field(body, "segment", 100),
            "dimension":clean_field(body, "dimension", 100),
            "observation":clean_field(body, "observation", 1800, required=True),
            "application":clean_field(body, "application", 1800),
            "verification":clean_field(body, "verification", 120, "手动记录", required=True),
        }
        with CATALOG_LOCK:
            catalog = read_catalog(self.root)
            asset = self.find_asset(key,catalog)
            cards = asset.setdefault("cards",[])
            if not isinstance(cards,list) or len(cards) >= 150:
                raise RequestError("这条素材的参考卡无法继续添加。")
            cards.append(card)
            save_catalog(self.root,catalog)
        self.respond(asset_response(self.root,asset), 201)


def main():
    parser = argparse.ArgumentParser(description="仅本机访问的视频素材工作台")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    def on_term(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    with WorkbenchServer(root, args.port) as server:
        print(f"http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
        finally:
            if jobs is not None and hasattr(jobs, "shutdown"):
                jobs.shutdown(root)

if __name__ == "__main__":
    sys.exit(main())
