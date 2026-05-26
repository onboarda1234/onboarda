from screening_complyadvantage.url_canonicalization import canonicalize_url


def test_canonicalize_lowercases_host_and_scheme():
    assert canonicalize_url("HTTPS://Example.COM/Path")["full_url"] == "https://example.com/Path"


def test_canonicalize_strips_default_http_port():
    assert canonicalize_url("http://example.com:80/a")["domain"] == "example.com"


def test_canonicalize_strips_default_https_port():
    assert canonicalize_url("https://example.com:443/a")["domain"] == "example.com"


def test_canonicalize_strips_trailing_slash_root_only():
    assert canonicalize_url("https://example.com/")["path"] == ""
    assert canonicalize_url("https://example.com/article/")["path"] == "/article/"


def test_canonicalize_preserves_path_segments_and_query():
    result = canonicalize_url("https://example.com/a/b?x=1")
    assert result["full_url"] == "https://example.com/a/b?x=1"
    assert result["path"] == "/a/b"


def test_canonicalize_drops_fragment():
    assert canonicalize_url("https://example.com/a#frag")["full_url"] == "https://example.com/a"


def test_canonicalize_handles_empty_input():
    assert canonicalize_url("  ") == {"full_url": "", "domain": "", "scheme": "", "path": ""}


def test_canonicalize_decomposes_into_four_fields():
    assert set(canonicalize_url("https://example.com/a")) == {"full_url", "domain", "scheme", "path"}
