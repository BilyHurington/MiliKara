"""Restricted HTTP client for lyric lookups (design §7).

* only an allowlist of music platform hosts, http/https, default ports;
* redirects are followed manually and every hop is re-validated;
* host names must resolve to public addresses only (no loopback / private /
  link-local / reserved targets) — or to the addresses proxy tools hand out in
  "Fake-IP" mode, which the proxy itself connects onwards;
* bounded response size and timeouts; no cookies are read or persisted and
  environment settings (netrc, proxies from env) are not consulted.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlsplit

import httpx

from .types import FetchError

ALLOWED_HOSTS = frozenset({
    "music.163.com",
    "y.music.163.com",
    "163cn.tv",
    "c.y.qq.com",
    "u.y.qq.com",
    "y.qq.com",
    "i.y.qq.com",
    "c6.y.qq.com",
    "y.gtimg.cn",  # QQ Music album covers
})
_ALLOWED_PATTERNS = (re.compile(r"^interface\d*\.music\.163\.com$"),
                     re.compile(r"^p\d+\.music\.126\.net$"))  # NetEase album covers

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

Resolver = Callable[[str], list[str]]


def is_allowed_host(host: Optional[str]) -> bool:
    if not host:
        return False
    host = host.lower().rstrip(".")
    return host in ALLOWED_HOSTS or any(p.match(host) for p in _ALLOWED_PATTERNS)


def system_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise FetchError(f"无法解析域名 {host}：{exc}") from exc
    return sorted({info[4][0] for info in infos})


# What proxy tools in "Fake-IP" mode (Clash / Mihomo / Surge / sing-box with TUN or an enhanced mode)
# answer for every name: the connection to it goes through the proxy, which reaches the real host.
# Accepted only for the music platforms' own names (the allowlist above), never a real private network:
# 198.18.0.0/15 is reserved for benchmarks; fc00::/18 (sing-box) and fdfe:dcba:9876::/64 (Mihomo) are
# those tools' default IPv6 ranges.
FAKE_IP_NETS = tuple(ipaddress.ip_network(n) for n in ("198.18.0.0/15", "fc00::/18", "fdfe:dcba:9876::/64"))


def is_fake_ip(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip.version == n.version and ip in n for n in FAKE_IP_NETS)


def is_public_ip(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified)


def validate_url(url: str, resolver: Resolver) -> None:
    """Raise :class:`FetchError` unless ``url`` is an allowed public target."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError(f"不支持的 URL 协议：{parts.scheme or '（无）'}")
    if parts.username or parts.password:
        raise FetchError("不允许包含账号密码的 URL")
    host = parts.hostname
    if not is_allowed_host(host):
        raise FetchError(f"不支持从该域名获取歌词：{host}")
    if parts.port not in (None, 80, 443):
        raise FetchError(f"不允许非标准端口：{parts.port}")
    try:
        ipaddress.ip_address(host)  # literal IPs are never on the allowlist anyway
        raise FetchError("不允许直接使用 IP 地址")
    except ValueError:
        pass
    addrs = resolver(host)
    if not addrs:
        raise FetchError(f"无法解析域名 {host}")
    bad = [a for a in addrs if not is_public_ip(a) and not is_fake_ip(a)]
    if bad:
        raise FetchError(f"{host} 被解析到了本机或局域网地址（{bad[0]}），为安全起见已拒绝访问。"
                         "可能是代理软件或网易云“解灰”之类的工具修改了 DNS / hosts："
                         "关闭这些工具后重试，或者直接粘贴歌词文本")


@dataclass
class SafeResponse:
    url: str
    status_code: int
    headers: httpx.Headers
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        body = self.text.strip()
        # QQ endpoints may answer JSONP: callback({...})
        m = re.match(r"^[\w$.]+\s*\((.*)\)\s*;?\s*$", body, re.S)
        if m:
            body = m.group(1)
        try:
            return json.loads(body)
        except ValueError as exc:
            raise FetchError(f"{urlsplit(self.url).hostname} 返回的不是 JSON") from exc


class SafeClient:
    def __init__(
        self,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        resolver: Optional[Resolver] = None,
        timeout: float = 10.0,
        max_bytes: int = 4 * 1024 * 1024,
        max_hops: int = 5,
    ) -> None:
        self.resolver = resolver or system_resolver
        self.max_bytes = max_bytes
        self.max_hops = max_hops
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": USER_AGENT},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SafeClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _send(self, method: str, url: str, read_body: bool, **kw: Any) -> tuple[httpx.Response, bytes]:
        validate_url(url, self.resolver)
        # never send or keep cookies between calls
        self._client.cookies.clear()
        try:
            with self._client.stream(method, url, **kw) as resp:
                body = b""
                if read_body and not resp.is_redirect:
                    chunks = []
                    size = 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise FetchError("响应过大，已拒绝")
                        chunks.append(chunk)
                    body = b"".join(chunks)
                return resp, body
        except httpx.HTTPError as exc:
            raise FetchError(f"连接 {urlsplit(url).hostname} 时网络错误：{exc}") from exc

    def request(self, method: str, url: str, **kw: Any) -> SafeResponse:
        """Send a request, following redirects hop by hop with re-validation."""
        current = url
        for _ in range(self.max_hops + 1):
            resp, body = self._send(method, current, True, **kw)
            if resp.is_redirect:
                loc = resp.headers.get("location")
                if not loc:
                    raise FetchError("重定向缺少目标地址")
                current = urljoin(current, loc)
                method = "GET" if method != "HEAD" else method
                kw.pop("content", None)
                kw.pop("json", None)
                kw.pop("data", None)
                continue
            if resp.status_code >= 400:
                raise FetchError(f"{urlsplit(current).hostname} answered HTTP {resp.status_code}")
            return SafeResponse(current, resp.status_code, resp.headers, body)
        raise FetchError("重定向次数过多")

    def get(self, url: str, **kw: Any) -> SafeResponse:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> SafeResponse:
        return self.request("POST", url, **kw)

    def follow_redirects(self, url: str, stop: Optional[Callable[[str], bool]] = None) -> str:
        """Resolve a short link hop by hop without downloading the target page.

        ``stop(url)`` may end the walk early once a location is recognized.
        """
        current = url
        for _ in range(self.max_hops + 1):
            if stop and current != url and stop(current):
                return current
            resp, _ = self._send("GET", current, False)
            if not resp.is_redirect:
                return current
            loc = resp.headers.get("location")
            if not loc:
                raise FetchError("重定向缺少目标地址")
            current = urljoin(current, loc)
        raise FetchError("重定向次数过多")
