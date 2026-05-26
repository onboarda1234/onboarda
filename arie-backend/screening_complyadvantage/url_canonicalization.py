"""URL canonicalization for adverse media articles and other provider URLs."""

from urllib.parse import urlparse, urlunparse


def canonicalize_url(url: str) -> dict[str, str]:
    """RFC 3986 canonical form + structural decomposition."""
    if not url or not url.strip():
        return {"full_url": "", "domain": "", "scheme": "", "path": ""}

    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower() if parsed.netloc else ""

    if scheme == "http" and host.endswith(":80"):
        host = host[:-3]
    elif scheme == "https" and host.endswith(":443"):
        host = host[:-4]

    path = parsed.path
    if path == "/":
        path = ""

    canonical = urlunparse((scheme, host, path, parsed.params, parsed.query, ""))
    return {"full_url": canonical, "domain": host, "scheme": scheme, "path": path}
