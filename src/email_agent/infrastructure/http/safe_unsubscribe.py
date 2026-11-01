"""Pinned HTTPS POST primitive, not exposed without verified list evidence.

Only application-generated, independently verified evidence enables this
transport. No route accepts a URL or a verification boolean from a client.
"""
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit


def bounded_public_dns(host, port, **kwargs):
    """Resolve both address families with explicit limits; uncertain DNS fails closed."""
    import dns.resolver
    records = []
    for family, kind in ((socket.AF_INET, "A"), (socket.AF_INET6, "AAAA")):
        try:
            answers = dns.resolver.resolve(host, kind, lifetime=2, search=False)
        except dns.resolver.NoAnswer:
            continue
        records.extend((family,socket.SOCK_STREAM,6,"",(answer.address,port)) for answer in answers)
    return records


def public_destination(url, resolver=bounded_public_dns):
    if not isinstance(url, str) or len(url) > 2000 or any(ord(c) < 33 for c in url):
        raise ValueError("Invalid unsubscribe URL")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.port not in (None, 443):
        raise ValueError("Only credential-free HTTPS on port 443 is allowed")
    host = parsed.hostname.encode("idna").decode("ascii")
    addresses = {r[4][0] for r in resolver(host, 443, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast or ipaddress.ip_address(ip).is_reserved for ip in addresses):
        raise ValueError("Unsubscribe destination is not exclusively public")
    return host, sorted(addresses)[0], (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=10, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connect to the checked address; TLS still verifies the original host.
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def post_verified_one_click(url, *, verified_dkim_coverage=False):
    if verified_dkim_coverage is not True:
        raise PermissionError("Independent DKIM coverage evidence is required")
    host, address, path = public_destination(url)
    connection = PinnedHTTPSConnection(host, address)
    try:
        connection.request("POST", path, body=b"List-Unsubscribe=One-Click", headers={
            "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Postmark-local/1"})
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise ValueError("Unsubscribe failed or redirected; redirects are never followed")
        response.read(16384)
        return {"state": "requested", "message": "Request accepted; delivery cessation is not confirmed"}
    finally:
        connection.close()
