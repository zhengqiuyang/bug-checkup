"""Differential scenario: X-Forwarded-For client attribution (proxy/forwarded_policy.py).

Protocol: each stdin line is ``{"peer": str, "xff": str, "cidrs": [str]}``; each stdout line
is the resolver's decision, so two environments can be compared line by line.

Security relevance: if client attribution shifts between versions, rate limits, audit logs and
allow-list decisions follow the wrong address.
"""

from __future__ import annotations

import json
import sys

from headroom.proxy.forwarded_policy import (
    header_first,
    normalize_ip,
    parse_cidr_list,
    peer_is_trusted_gateway,
)


def resolve(case: dict) -> dict:
    try:
        cidrs = parse_cidr_list(",".join(case.get("cidrs") or []))
        peer = case.get("peer")
        xff = case.get("xff", "")
        return {
            "trusted": peer_is_trusted_gateway(peer, cidrs),
            "normalized_peer": str(normalize_ip(peer) or ""),
            "first_hop": header_first(xff),
            "cidr_count": len(cidrs),
        }
    except Exception as exc:  # noqa: BLE001 - an exception is a diffable outcome
        return {"error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        print(json.dumps(resolve(json.loads(line)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
