"""Persistent, privacy-preserving limits for public account endpoints.

The database stores only keyed hashes of the request source and normalized
phone. The HMAC key lives on the server. In-memory counters are not reliable
across Function Compute instances or cold starts.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import time


class RateLimitExceeded(Exception):
    pass


class RateLimiter:
    # Fixed windows are deliberately conservative. An attacker can block a
    # phone temporarily, but cannot enumerate it from the response or DB key.
    LIMITS = {
        "login": {"ip": (30, 600), "phone": (10, 600)},
        "register": {"ip": (20, 3600), "phone": (5, 3600)},
    }

    def __init__(self, repository, key: str):
        if len(key.encode("utf-8")) < 32:
            raise ValueError("限流密钥不足 32 字节")
        self.repository = repository
        self.key = key.encode("utf-8")

    def _bucket(self, operation: str, kind: str, value: str) -> str:
        return hmac.new(self.key, f"{operation}:{kind}:{value}".encode("utf-8"),
                        hashlib.sha256).hexdigest()

    def consume(self, operation: str, source_ip: str, phone: str | None) -> None:
        if operation not in self.LIMITS:
            raise ValueError("未知的账号操作")
        try:
            canonical_ip = str(ipaddress.ip_address(source_ip))
        except ValueError:
            # Never trust user-controlled X-Forwarded-For. If ASGI does not
            # expose a valid source IP, all requests share a conservative key.
            canonical_ip = "unknown"
        limits = self.LIMITS[operation]
        buckets = [(self._bucket(operation, "ip", canonical_ip), *limits["ip"])]
        if phone:
            buckets.append((self._bucket(operation, "phone", phone), *limits["phone"]))
        self.repository.consume_rate_limits(sorted(buckets), int(time.time()))
