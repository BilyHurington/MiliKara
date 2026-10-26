import json

import httpx
import pytest

from kara_align.lyrics.fetch import (
    CollectionListing,
    FetchedSong,
    FetchError,
    SafeClient,
    fetch_lyrics_from_link,
    parse_target,
    resolve_link,
)
from kara_align.lyrics.fetch.netease import normalize_netease_lrc

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


def client(handler, resolver=PUBLIC):
    return SafeClient(transport=httpx.MockTransport(handler), resolver=resolver)


@pytest.mark.parametrize("text,expected", [
    ("https://music.163.com/#/song?id=1234", ("netease", "song", "1234")),
    ("https://music.163.com/song?id=1234&userid=9", ("netease", "song", "1234")),
    ("https://y.music.163.com/m/song?id=55", ("netease", "song", "55")),
    ("https://music.163.com/song/66/?userid=1", ("netease", "song", "66")),
    ("https://music.163.com/#/album?id=77", ("netease", "album", "77")),
    ("https://music.163.com/playlist?id=88", ("netease", "playlist", "88")),
    ("分享Aimer的单曲《カタオモイ》: https://music.163.com/song?id=4321&userid=1 (来自@网易云音乐)",
     ("netease", "song", "4321")),
    ("https://y.qq.com/n/ryqq/songDetail/003abcDEF", ("qq", "song", "003abcDEF")),
    ("https://y.qq.com/n/yqq/song/002xyz.html", ("qq", "song", "002xyz")),
    ("https://i.y.qq.com/v8/playsong.html?songmid=001mid&ADTAG=x", ("qq", "song", "001mid")),
    ("https://i.y.qq.com/v8/playsong.html?songid=123456", ("qq", "song", "123456")),
    ("https://y.qq.com/n/ryqq/albumDetail/004alb", ("qq", "album", "004alb")),
    ("https://y.qq.com/n/ryqq/playlist/7777", ("qq", "playlist", "7777")),
    ("netease:1234", ("netease", "song", "1234")),
    ("qq:003abc", ("qq", "song", "003abc")),
    ("netease:album:99", ("netease", "album", "99")),
])
def test_parse_links(text, expected):
    t = parse_target(text)
    assert (t.platform, t.kind, t.id) == expected


def test_unsupported_and_ambiguous():
    with pytest.raises(FetchError):
        resolve_link("12345")
    with pytest.raises(FetchError):
        resolve_link("https://example.com/song?id=1")
    with pytest.raises(FetchError):
        resolve_link("no link here")


def test_short_link_resolution_hop_by_hop():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if req.url.host == "163cn.tv":
            return httpx.Response(302, headers={"location": "https://y.music.163.com/m/song?id=999&uct=1"})
        raise AssertionError("target page must not be fetched")

    t = resolve_link("分享 https://163cn.tv/abcd (来自@网易云音乐)", client(handler))
    assert (t.platform, t.kind, t.id) == ("netease", "song", "999")
    assert seen == ["https://163cn.tv/abcd"]


def test_short_link_redirect_to_disallowed_host():
    def handler(req):
        if req.url.host == "163cn.tv":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
        raise AssertionError("must not be contacted")

    with pytest.raises(FetchError):
        resolve_link("https://163cn.tv/evil", client(handler))


def test_redirect_to_other_host_refused_in_request():
    def handler(req):
        return httpx.Response(302, headers={"location": "https://evil.example.com/x"})

    with pytest.raises(FetchError, match="不支持"):
        client(handler).get("https://music.163.com/api/x")


def test_private_dns_refused():
    def handler(req):
        raise AssertionError("must not be contacted")

    for addr in ("127.0.0.1", "10.0.0.5", "192.168.1.2", "169.254.1.1", "::1", "::ffff:127.0.0.1", "fd12:3456::1"):
        with pytest.raises(FetchError, match="本机或局域网地址"):
            client(handler, resolver=lambda h, a=addr: [a]).get("https://music.163.com/api/x")
    # a fake address next to a real private one: still refused
    with pytest.raises(FetchError, match="10.0.0.5"):
        client(handler, resolver=lambda h: ["198.18.0.7", "10.0.0.5"]).get("https://music.163.com/api/x")


def test_a_proxy_in_fake_ip_mode_is_used():
    """Clash / Mihomo / sing-box with Fake-IP answer 198.18.x.x (and their own IPv6 ranges) for every
    name; the proxy reaches the real host.  Allowed for the music platforms' names."""
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json={"ok": True})

    for addrs in (["198.18.0.12"], ["198.19.255.1"], ["198.18.3.4", "fc00::18"], ["fdfe:dcba:9876::5"]):
        assert client(handler, resolver=lambda h, a=addrs: a).get("https://music.163.com/api/x").json() == {"ok": True}
    assert len(seen) == 4
    # the allowlist still applies
    with pytest.raises(FetchError, match="不支持"):
        client(handler, resolver=lambda h: ["198.18.0.12"]).get("https://example.com/x")


def test_scheme_port_and_credentials_refused():
    c = client(lambda r: httpx.Response(200))
    for url in ("file:///etc/passwd", "ftp://music.163.com/x", "https://music.163.com:8080/x",
                "https://user:pw@music.163.com/x", "https://127.0.0.1/x"):
        with pytest.raises(FetchError):
            c.get(url)


def test_size_cap():
    c = SafeClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 2000)),
                   resolver=PUBLIC, max_bytes=1000)
    with pytest.raises(FetchError, match="过大"):
        c.get("https://music.163.com/api/x")


def test_too_many_redirects():
    c = client(lambda r: httpx.Response(302, headers={"location": "https://music.163.com/loop"}))
    with pytest.raises(FetchError, match="重定向"):
        c.get("https://music.163.com/start")


def test_no_cookies_sent():
    def handler(req):
        assert "cookie" not in req.headers
        return httpx.Response(200, headers={"set-cookie": "a=b; Path=/"}, json={})

    c = client(handler)
    c.get("https://music.163.com/api/a")
    c.get("https://music.163.com/api/b")


def netease_handler(req):
    path = req.url.path
    if path == "/api/song/detail/":
        return httpx.Response(200, json={"code": 200, "songs": [
            {"id": 1234, "name": "曲", "artists": [{"name": "歌手"}], "album": {"name": "盤"}, "duration": 200000}]})
    if path == "/api/song/lyric":
        assert req.url.params["id"] == "1234"
        return httpx.Response(200, json={
            "code": 200,
            "lrc": {"lyric": '{"t":0,"c":[{"tx":"作词: "},{"tx":"誰か"}]}\n[00:12.30]君と歩いた\n'},
            "tlyric": {"lyric": "[00:12.30]和你一起走过\n"},
            "romalrc": {"lyric": ""},
        })
    if path == "/api/v1/album/77":
        return httpx.Response(200, json={"code": 200, "album": {"name": "A"}, "songs": [
            {"id": 1, "name": "x", "ar": [{"name": "y"}], "dt": 1000}]})
    return httpx.Response(404)


def test_netease_song():
    song = fetch_lyrics_from_link("https://music.163.com/#/song?id=1234", client(netease_handler))
    assert isinstance(song, FetchedSong)
    assert song.title == "曲" and song.artists == ["歌手"] and song.duration_ms == 200000
    assert set(song.tracks) == {"original", "translation"}
    assert song.has_timestamps["original"]
    assert song.tracks["original"].startswith("[00:00.000]作词: 誰か")
    snap = song.to_snapshot("translation")
    assert snap.origin == "netease" and snap.kind == "translation" and snap.platform_song_id == "1234"


def test_netease_album_listing():
    listing = fetch_lyrics_from_link("netease:album:77", client(netease_handler))
    assert isinstance(listing, CollectionListing)
    assert listing.title == "A" and listing.songs[0].song_id == "1"


def test_netease_error_code():
    c = client(lambda r: httpx.Response(200, json={"code": -460, "message": "cheating"}))
    with pytest.raises(FetchError, match="-460"):
        fetch_lyrics_from_link("netease:5", c)


def test_normalize_netease_json_lines_keeps_platform_time():
    out = normalize_netease_lrc('{"t":1500,"c":[{"tx":"作曲: "},{"tx":"X"}]}\n[00:02.00]a')
    assert out.split("\n")[0] == "[00:01.500]作曲: X"


def test_qq_song_plain_text_not_faked():
    def handler(req):
        if req.url.host == "u.y.qq.com":
            body = json.loads(req.content)
            assert body["req"]["param"]["song_mid"] == "003abc"
            return httpx.Response(200, json={"code": 0, "req": {"code": 0, "data": {"track_info": {
                "mid": "003abc", "title": "歌", "singer": [{"name": "S"}], "album": {"name": "Al"},
                "interval": 180}}}})
        assert req.headers["referer"] == "https://y.qq.com/"
        return httpx.Response(200, text='MusicJsonCallback({"retcode":0,"lyric":"君と&#10;歩いた","trans":""})')

    song = fetch_lyrics_from_link("https://y.qq.com/n/ryqq/songDetail/003abc", client(handler))
    assert song.title == "歌" and song.duration_ms == 180_000
    assert song.tracks["original"] == "君と\n歩いた"
    assert song.has_timestamps["original"] is False
    assert song.to_snapshot().kind == "lyrics"


@pytest.mark.network
def test_live_netease():  # pragma: no cover - optional
    song = fetch_lyrics_from_link("netease:1901371647")
    assert song.tracks
