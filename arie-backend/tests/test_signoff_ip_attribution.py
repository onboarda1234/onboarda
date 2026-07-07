"""P10-6 / RDI-012 — sign-off IP attribution trusts proxy headers only from a
trusted proxy peer.

get_client_ip already gated X-Forwarded-For on the direct peer being a
private/loopback proxy, but the X-Real-IP fallback was UNCONDITIONAL — a
direct public caller could stamp an arbitrary IP into officer sign-off audit
provenance by sending the header. Both headers are now honoured only when
request.remote_ip is a trusted (private/loopback) proxy hop.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from base_handler import BaseHandler


def _handler(remote_ip, headers=None):
    class _Req:
        pass

    h = BaseHandler.__new__(BaseHandler)
    h.request = _Req()
    h.request.remote_ip = remote_ip
    h.request.headers = headers or {}
    return h


# NOTE: not a TEST-NET address — Python's ipaddress marks the RFC 5737
# TEST-NET ranges as is_private=True, which would make the "public peer"
# accidentally trusted. 8.8.8.8 is unambiguously public.
PUBLIC_PEER = "8.8.8.8"           # a direct internet caller
PROXY_PEER = "10.0.4.233"         # ALB/ECS private hop
SPOOFED = "198.51.100.99"


class TestXRealIpTrustBoundary:
    def test_public_peer_cannot_spoof_via_x_real_ip(self):
        h = _handler(PUBLIC_PEER, {"X-Real-IP": SPOOFED})
        assert h.get_client_ip() == PUBLIC_PEER

    def test_trusted_proxy_x_real_ip_is_honoured(self):
        h = _handler(PROXY_PEER, {"X-Real-IP": SPOOFED})
        assert h.get_client_ip() == SPOOFED

    def test_loopback_peer_is_trusted(self):
        h = _handler("127.0.0.1", {"X-Real-IP": SPOOFED})
        assert h.get_client_ip() == SPOOFED

    def test_garbage_x_real_ip_ignored(self):
        h = _handler(PROXY_PEER, {"X-Real-IP": "<script>alert(1)</script>"})
        assert h.get_client_ip() == PROXY_PEER


class TestXForwardedForTrustBoundary:
    def test_public_peer_cannot_spoof_via_xff(self):
        h = _handler(PUBLIC_PEER, {"X-Forwarded-For": f"{SPOOFED}, 10.0.0.1"})
        assert h.get_client_ip() == PUBLIC_PEER

    def test_trusted_proxy_xff_leftmost_wins(self):
        h = _handler(PROXY_PEER, {"X-Forwarded-For": f"{SPOOFED}, 10.0.0.1"})
        assert h.get_client_ip() == SPOOFED

    def test_xff_takes_precedence_over_x_real_ip_behind_proxy(self):
        h = _handler(PROXY_PEER, {
            "X-Forwarded-For": "192.0.2.7",
            "X-Real-IP": SPOOFED,
        })
        assert h.get_client_ip() == "192.0.2.7"


class TestNoHeaders:
    def test_bare_peer_returned(self):
        assert _handler(PUBLIC_PEER).get_client_ip() == PUBLIC_PEER
        assert _handler(PROXY_PEER).get_client_ip() == PROXY_PEER
