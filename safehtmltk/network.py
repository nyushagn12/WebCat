from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
import socket
import ssl
import zlib
from typing import Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit
import http.client


MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BODY_BYTES = 512 * 1024
MAX_HEADER_BYTES = 64 * 1024
MAX_REDIRECTS = 8
DEFAULT_TIMEOUT = 15.0
USER_AGENT = "WebCat/0.4 (small Python HTML/CSS engine)"


_FORBIDDEN_AUTHOR_HEADERS = {
    "connection", "content-length", "cookie", "cookie2", "host", "origin",
    "proxy-authorization", "proxy-authenticate", "referer", "te", "trailer",
    "transfer-encoding", "upgrade", "user-agent", "accept-encoding",
}
_CORS_SAFE_METHODS = {"GET", "HEAD", "POST"}
_CORS_SAFE_CONTENT_TYPES = {
    "application/x-www-form-urlencoded", "multipart/form-data", "text/plain",
}


class NetworkError(RuntimeError):
    pass


@dataclass(frozen=True)
class WebResponse:
    url: str
    status: int
    reason: str
    headers: dict[str, str]
    body: bytes

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").lower()

    def text(self) -> str:
        return decode_text(self.body, self.content_type)


def origin_of(url: str) -> str:
    p = urlsplit(url)
    if p.scheme not in {"http", "https"}:
        return "null"
    host = p.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = p.port
    default = 443 if p.scheme == "https" else 80
    port_part = "" if port in {None, default} else f":{port}"
    return f"{p.scheme}://{host.lower()}{port_part}"


def same_origin(a: str, b: str) -> bool:
    return origin_of(a) == origin_of(b)


def resolve_url(base: str, ref: str) -> str:
    ref = str(ref or "").strip()
    value = urljoin(base, ref)
    p = urlsplit(value)
    if p.scheme not in {"http", "https"}:
        return value
    # Strip URL fragments before HTTP requests; fragments never reach a server.
    return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))


def decode_text(data: bytes, content_type: str = "") -> str:
    match = re.search(r"charset\s*=\s*['\"]?\s*([A-Za-z0-9._-]+)", content_type, re.I)
    charset = match.group(1) if match else "utf-8"
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def _is_non_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    return not ip.is_global


def _resolve_addresses(hostname: str, port: int, *, allow_private: bool) -> list[tuple[int, str]]:
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NetworkError(f"DNS lookup failed for {hostname}: {exc}") from exc

    out: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        address = sockaddr[0]
        key = (family, address)
        if key in seen:
            continue
        seen.add(key)
        if _is_non_public_address(address) and not allow_private:
            continue
        out.append(key)
    if not out:
        raise NetworkError(f"Refused network target {hostname!r}: resolved addresses are private/reserved")
    return out


def _author_header_names(headers: Mapping[str, str]) -> list[str]:
    names: list[str] = []
    for name, value in headers.items():
        key = str(name).strip().lower()
        if not key or any(ch in key for ch in "\r\n:"):
            raise NetworkError("Invalid HTTP header name")
        if key in _FORBIDDEN_AUTHOR_HEADERS or key.startswith("sec-") or key.startswith("proxy-"):
            raise NetworkError(f"HTTP header is not available to page JavaScript: {name}")
        text_value = str(value)
        if "\r" in text_value or "\n" in text_value:
            raise NetworkError("Invalid HTTP header value")
        names.append(key)
    return sorted(set(names))


def _cors_safe_header(name: str, value: str) -> bool:
    key = name.lower()
    value = value.strip()
    if key in {"accept", "accept-language", "content-language"}:
        return True
    if key == "content-type":
        media_type = value.split(";", 1)[0].strip().lower()
        return media_type in _CORS_SAFE_CONTENT_TYPES
    return False


def _needs_cors_preflight(method: str, headers: Mapping[str, str]) -> bool:
    if method.upper() not in _CORS_SAFE_METHODS:
        return True
    return any(not _cors_safe_header(str(k), str(v)) for k, v in headers.items())


def _check_cors_headers(response: WebResponse, origin: str, method: str | None = None, header_names: list[str] | None = None) -> None:
    acao = response.headers.get("access-control-allow-origin", "").strip()
    if acao not in {"*", origin}:
        raise NetworkError("Cross-origin response did not grant access through CORS")
    if method is not None:
        acam = {x.strip().upper() for x in response.headers.get("access-control-allow-methods", "").split(",") if x.strip()}
        if "*" not in acam and method.upper() not in acam:
            raise NetworkError("CORS preflight did not allow the requested method")
    if header_names:
        acah = {x.strip().lower() for x in response.headers.get("access-control-allow-headers", "").split(",") if x.strip()}
        if "*" not in acah and any(name.lower() not in acah for name in header_names):
            raise NetworkError("CORS preflight did not allow the requested headers")


def _decode_content_encoding(data: bytes, encoding: str, limit: int) -> bytes:
    tokens = [part.strip().lower() for part in encoding.split(",") if part.strip()]
    if not tokens or tokens == ["identity"]:
        return data
    if any(token == "br" for token in tokens):
        raise NetworkError("Brotli HTTP responses are not enabled in the standard-library build")
    result = data
    for token in reversed(tokens):
        if token == "identity":
            continue
        if token not in {"gzip", "deflate"}:
            raise NetworkError(f"Unsupported HTTP content-encoding: {token}")
        result = _inflate_limited(result, token, limit)
    return result


def _inflate_limited(data: bytes, encoding: str, limit: int) -> bytes:
    wb = 16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
    try:
        dec = zlib.decompressobj(wb)
        out = bytearray()
        pos = 0
        while pos < len(data):
            chunk = data[pos:pos + 64 * 1024]
            pos += len(chunk)
            room = limit + 1 - len(out)
            if room <= 0:
                raise NetworkError("Decoded HTTP response is larger than the WebCat response limit")
            out.extend(dec.decompress(chunk, room))
            if len(out) > limit:
                raise NetworkError("Decoded HTTP response is larger than the WebCat response limit")
        room = limit + 1 - len(out)
        out.extend(dec.flush(room))
        if len(out) > limit:
            raise NetworkError("Decoded HTTP response is larger than the WebCat response limit")
        if not dec.eof:
            raise NetworkError("Truncated compressed HTTP response")
        return bytes(out)
    except zlib.error as exc:
        if encoding == "deflate":
            try:
                dec = zlib.decompressobj(-zlib.MAX_WBITS)
                out = bytearray()
                pos = 0
                while pos < len(data):
                    chunk = data[pos:pos + 64 * 1024]
                    pos += len(chunk)
                    room = limit + 1 - len(out)
                    out.extend(dec.decompress(chunk, room))
                    if len(out) > limit:
                        raise NetworkError("Decoded HTTP response is larger than the WebCat response limit")
                out.extend(dec.flush(limit + 1 - len(out)))
                if len(out) > limit or not dec.eof:
                    raise NetworkError("Invalid/truncated deflate HTTP response")
                return bytes(out)
            except zlib.error:
                pass
        raise NetworkError(f"Invalid {encoding} HTTP response: {exc}") from exc


class _NoProxyConnection:
    """Small direct HTTP client; intentionally bypasses proxy environment variables."""

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
        timeout: float = DEFAULT_TIMEOUT,
        allow_private: bool = False,
    ) -> WebResponse:
        p = urlsplit(url)
        if p.scheme not in {"http", "https"}:
            raise NetworkError("Only http:// and https:// URLs are supported")
        if p.username is not None or p.password is not None:
            raise NetworkError("URLs containing username/password are not supported")
        host = p.hostname
        if not host:
            raise NetworkError("URL has no hostname")
        if len(host) > 253:
            raise NetworkError("Hostname is too long")
        try:
            port = p.port or (443 if p.scheme == "https" else 80)
        except ValueError as exc:
            raise NetworkError("Invalid URL port") from exc

        method = method.upper()
        if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
            raise NetworkError(f"Unsupported HTTP method: {method}")
        if len(body) > MAX_REQUEST_BODY_BYTES:
            raise NetworkError("HTTP request body is too large")

        addresses = _resolve_addresses(host, port, allow_private=allow_private)
        last_error: Exception | None = None
        for family, address in addresses:
            sock: socket.socket | None = None
            try:
                sock = socket.socket(family, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                sockaddr = (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
                sock.connect(sockaddr)
                if p.scheme == "https":
                    context = ssl.create_default_context()
                    sock = context.wrap_socket(sock, server_hostname=host)

                if p.hostname and ":" in host and not host.startswith("["):
                    host_header = f"[{host}]"
                else:
                    host_header = host
                if port not in {80, 443}:
                    host_header = f"{host_header}:{port}"

                path = p.path or "/"
                if p.query:
                    path += "?" + p.query
                req_headers = {
                    "Host": host_header,
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,text/css,application/javascript,text/javascript,application/json;q=0.9,*/*;q=0.5",
                    "Accept-Encoding": "gzip, deflate, identity",
                    "Connection": "close",
                }
                for k, v in (headers or {}).items():
                    if "\r" in k or "\n" in k or "\r" in str(v) or "\n" in str(v):
                        raise NetworkError("Invalid HTTP header")
                    req_headers[str(k)] = str(v)
                if body and "Content-Length" not in {k.title(): v for k, v in req_headers.items()} and "content-length" not in {k.lower() for k in req_headers}:
                    req_headers["Content-Length"] = str(len(body))

                request_lines = [f"{method} {path} HTTP/1.1"]
                request_lines += [f"{k}: {v}" for k, v in req_headers.items()]
                wire = ("\r\n".join(request_lines) + "\r\n\r\n").encode("latin-1", "strict") + body
                sock.sendall(wire)

                response = http.client.HTTPResponse(sock, method=method)
                response.begin()
                header_bytes = sum(len(k) + len(v) for k, v in response.getheaders())
                if header_bytes > MAX_HEADER_BYTES:
                    raise NetworkError("HTTP response headers are too large")
                raw_headers = {k.lower(): v.strip() for k, v in response.getheaders()}
                body_out = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body_out) > MAX_RESPONSE_BYTES:
                    raise NetworkError("HTTP response is larger than the WebCat compressed-response limit")
                body_out = _decode_content_encoding(body_out, raw_headers.get("content-encoding", ""), MAX_RESPONSE_BYTES)
                return WebResponse(
                    url=url,
                    status=int(response.status),
                    reason=str(response.reason or ""),
                    headers=raw_headers,
                    body=body_out,
                )
            except Exception as exc:
                last_error = exc
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass
        raise NetworkError(f"Connection failed for {host}: {last_error}") from last_error


def fetch(
    url: str,
    *,
    origin: str | None = None,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    body: bytes = b"",
    timeout: float = DEFAULT_TIMEOUT,
    allow_private: bool = False,
    max_redirects: int = MAX_REDIRECTS,
    require_cors: bool = False,
    cors_mode: str = "cors",
) -> WebResponse:
    client = _NoProxyConnection()
    current = resolve_url(url, url)
    author_headers = dict(headers or {})
    _author_header_names(author_headers)
    request_headers = dict(author_headers)
    if origin and origin != "null":
        request_headers.setdefault("Origin", origin)

    for _ in range(max_redirects + 1):
        p = urlsplit(current)
        # Never carry an Authorization header across a different origin.
        if origin and not same_origin(origin + "/" if "://" in origin else origin, current):
            request_headers.pop("Authorization", None)
            author_headers.pop("Authorization", None)

        author_header_names = _author_header_names(author_headers)
        cross_origin = bool(origin and not same_origin(origin + "/", current))
        if cross_origin and cors_mode == "same-origin":
            raise NetworkError("Cross-origin request blocked by same-origin policy")
        if cross_origin and cors_mode == "no-cors" and _needs_cors_preflight(method, request_headers):
            raise NetworkError("no-cors fetch may only use a CORS-safelisted method and headers")
        if cross_origin and cors_mode == "cors" and _needs_cors_preflight(method, request_headers):
            preflight_headers = {
                "Origin": origin or "",
                "Access-Control-Request-Method": method.upper(),
            }
            if author_header_names:
                preflight_headers["Access-Control-Request-Headers"] = ", ".join(author_header_names)
            preflight = client.request(
                current, method="OPTIONS", headers=preflight_headers, body=b"",
                timeout=timeout, allow_private=allow_private,
            )
            if not (200 <= preflight.status < 300):
                raise NetworkError(f"CORS preflight failed with HTTP {preflight.status}")
            _check_cors_headers(preflight, origin or "", method, author_header_names)

        response = client.request(
            current,
            method=method,
            headers=request_headers,
            body=body,
            timeout=timeout,
            allow_private=allow_private,
        )
        if response.status not in {301, 302, 303, 307, 308}:
            if require_cors and origin and not same_origin(origin + "/", current):
                _check_cors_headers(response, origin)
            return WebResponse(current, response.status, response.reason, response.headers, response.body)

        location = response.headers.get("location")
        if not location:
            return response
        next_url = resolve_url(current, location)
        np = urlsplit(next_url)
        if np.scheme not in {"http", "https"}:
            raise NetworkError("Redirected to a non-HTTP URL")
        # Follow redirects only when the target can pass the same network policy.
        current = next_url
        if response.status in {301, 302, 303} and method == "POST":
            method = "GET"
            body = b""
    raise NetworkError("Too many HTTP redirects")
