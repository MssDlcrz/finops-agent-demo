"""Send an agent's outbound calls through the Trustabl egress gateway.

When this agent runs in a Trustabl sandbox whose hosts are routed through the
gateway, it cannot reach those hosts directly: its only way out is the gateway,
which checks a short claim attached to every call (which approved task, which
tool, where the call goes, how risky) and forwards the call only if the claim
matches. See Trustabl's docs/RUNTIME_CLAIM_CHECKER_GUIDE.md.

Trustabl's launch sets two variables in the sandbox:

  TRUSTABL_EGRESS_URL    where the gateway is, e.g. http://host.openshell.internal:3004
  TRUSTABL_EGRESS_TOKEN  this sandbox's credential (it tells the gateway which agent is calling)

Outside a Trustabl sandbox neither is set, and calls go straight to the API, so
the agent still runs on a laptop.

Python standard library only.

    from trustabl_egress import call, Refused

    try:
        status, body = call("POST", "https://api.example.com/refunds", tool="issue_refund",
                            operation="write", risk="medium", json_body={"order": "A-4471"})
    except Refused as e:
        ...  # e.code is "denied", "review_pending", "unauthenticated" or "check_unavailable"
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

SCHEMA_VERSION = "trustabl-claim-0.1"

# Codes the gateway answers with when it does not forward a call. Anything
# else, including a 4xx, came from the API itself.
REFUSAL_CODES = {"denied", "review_pending", "unauthenticated", "check_unavailable", "bad_request", "upstream_error"}


class Refused(Exception):
    """The gateway did not forward the call. The reasons are in Trustabl's
    decision record (the sandbox's Claim checks tab), never sent back here."""

    def __init__(self, code, decision_id=""):
        super().__init__(f"Trustabl refused the call: {code}")
        self.code = code
        self.decision_id = decision_id


def gateway_configured():
    return bool(os.environ.get("TRUSTABL_EGRESS_URL") and os.environ.get("TRUSTABL_EGRESS_TOKEN"))


def _host_port(url):
    u = urllib.parse.urlsplit(url)
    return (u.hostname or "").lower(), u.port or (443 if u.scheme == "https" else 80)


def _covers(entry, host, port):
    """Whether a task's allowed destination (host, host:port, *.suffix[:port])
    covers host:port, the same rule the gateway applies."""
    h, _, p = entry.lower().partition(":")
    if p and int(p) != port:
        return False
    if h.startswith("*."):
        return host.endswith(h[1:])
    return h == host


def tasks():
    """The sandbox's active tasks, as the gateway lists them."""
    req = urllib.request.Request(
        os.environ["TRUSTABL_EGRESS_URL"].rstrip("/") + "/tasks",
        headers={"X-Trustabl-Bind-Token": os.environ["TRUSTABL_EGRESS_TOKEN"]},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r).get("tasks", [])
    except urllib.error.HTTPError as e:
        raise Refused(_refusal_code(e)) from None


def _task_for(host, port):
    """The task a call belongs to: TRUSTABL_TASK_ID when it is set, otherwise
    the first active task that allows this destination. Read fresh on every
    call, so a task approved or changed a moment ago is picked up."""
    wanted = os.environ.get("TRUSTABL_TASK_ID", "")
    for t in tasks():
        if wanted and t["id"] == wanted:
            return t
        if not wanted and any(_covers(d, host, port) for d in t.get("allowedDestinations", [])):
            return t
    return None


def claim_for(url, tool, operation, risk):
    """The claim for one call. With no matching task the claim names none,
    and the gateway holds the call for a person to review."""
    host, port = _host_port(url)
    task = _task_for(host, port)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "taskId": task["id"] if task else "",
        "selectedTool": tool,
        "expectedEffect": {"operation": operation, "destination": f"{host}:{port}"},
        "riskTier": risk,
        "evidenceRefs": [{"type": "task", "digest": task["digest"]}] if task else [],
    }


def _refusal_code(err):
    try:
        return json.loads(err.read()).get("code", "denied")
    except Exception:
        return "denied"


def call(method, url, *, tool, operation="read", risk="low", json_body=None, headers=None, timeout=30):
    """Make one HTTP call, through the gateway when this is a Trustabl sandbox.

    Returns (status, body text). Raises Refused when the gateway does not
    forward the call. An error status from the API itself is returned, not
    raised.
    """
    data = json.dumps(json_body).encode() if json_body is not None else None
    hdrs = dict(headers or {})
    if data is not None:
        hdrs.setdefault("Content-Type", "application/json")

    if not gateway_configured():
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    else:
        claim = claim_for(url, tool, operation, risk)
        hdrs.update({
            "X-Trustabl-Bind-Token": os.environ["TRUSTABL_EGRESS_TOKEN"],
            "X-Trustabl-Target": url,
            "X-Trustabl-Claim": base64.urlsafe_b64encode(json.dumps(claim).encode()).decode().rstrip("="),
        })
        req = urllib.request.Request(
            os.environ["TRUSTABL_EGRESS_URL"].rstrip("/") + "/forward", data=data, headers=hdrs, method=method
        )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        decision = e.headers.get("X-Trustabl-Decision", "")
        body = e.read()
        if gateway_configured() and e.code in (400, 403, 502, 503):
            try:
                code = json.loads(body).get("code")
            except Exception:
                code = None
            if code in REFUSAL_CODES:
                raise Refused(code, decision) from None
        return e.code, body.decode(errors="replace")
