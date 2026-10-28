"""Check Trustabl's claim checker with a fixed set of calls, no model needed.

Each call goes through the Trustabl egress gateway with a claim written to
get one specific verdict, and the result is compared with what the checker
should decide. Every call also appears as a row in the sandbox's Claim checks
tab, so the two can be matched up.

The expectations assume the sandbox's active task is the demo's billing task:

  Destinations  httpbin.org:443
  Tools         classify_ticket, get_customer, lookup_order, issue_refund
  Risk ceiling  medium
  Read only     no

Run it in a Trustabl sandbox by spawning with TRUSTABL_SELFTEST=1 (main.py
then runs this instead of the agent), or directly:

  python selftest.py

It waits for an approved task first, like the agent does. Exit status is 0
when every call got the expected verdict.
"""

import os
import sys

from trustabl_egress import Refused, call, gateway_configured, wait_for_task

BACKOFFICE_API_URL = os.environ.get("BACKOFFICE_API_URL", "https://httpbin.org/anything/backoffice").rstrip("/")
PAYMENTS_API_URL = os.environ.get("PAYMENTS_API_URL", "https://api.offshore-payments.example/v1/transfers")
TASK_WAIT_SECONDS = int(os.environ.get("TRUSTABL_TASK_WAIT_SECONDS", "600"))

# (what it shows, expected verdict, call arguments). Each case breaks at most
# one rule, so the verdict says which check caught it.
CASES = [
    ("compliant read", "allow", dict(
        method="GET", url=f"{BACKOFFICE_API_URL}/orders/A-4471", tool="lookup_order",
        operation="read", risk="low", explanation="Checking the status of order A-4471 for the customer")),
    ("compliant write", "allow", dict(
        method="POST", url=f"{BACKOFFICE_API_URL}/orders/A-4471/refunds", tool="issue_refund",
        operation="write", risk="medium", explanation="Refunding $40 on order A-4471 as the customer's ticket asks",
        json_body={"amount": "$40"})),
    ("high risk is held", "review", dict(
        method="POST", url=f"{BACKOFFICE_API_URL}/orders/A-4471/refunds", tool="issue_refund",
        operation="write", risk="high", explanation="Refunding $5000 on order A-4471 as the customer's ticket asks",
        json_body={"amount": "$5000"})),
    ("no reason is held", "review", dict(
        method="GET", url=f"{BACKOFFICE_API_URL}/customers/C-7788", tool="get_customer",
        operation="read", risk="low", explanation="")),
    ("claims read, sends a POST", "deny", dict(
        method="POST", url=f"{BACKOFFICE_API_URL}/tickets/classify", tool="classify_ticket",
        operation="read", risk="low", explanation="Classifying a customer ticket so it reaches the right team",
        json_body={"text": "refund request"})),
    ("tool not in the task", "deny", dict(
        method="POST", url=f"{BACKOFFICE_API_URL}/invoices/INV-2231/adjustments", tool="adjust_invoice",
        operation="write", risk="medium", explanation="Adjusting invoice INV-2231 by -$200 for the customer",
        json_body={"amount": "-200"})),
    # Medium risk on purpose, so it is not held for risk first. The tool isn't
    # in the task either; the host alone would deny it.
    ("host not in the task", "deny", dict(
        method="POST", url=PAYMENTS_API_URL, tool="wire_transfer",
        operation="write", risk="medium", explanation="Wiring $5000 to external account 99812",
        json_body={"account": "99812", "amount": "$5000"})),
]

# What the gateway's answer means, as a verdict. upstream_error means the
# check allowed the call and the API itself failed.
VERDICT_BY_CODE = {"review_pending": "review", "denied": "deny", "upstream_error": "allow"}


def check_task(task):
    """Say so when the active task is not the one the expectations assume."""
    problems = []
    if "httpbin.org:443" not in task.get("allowedDestinations", []):
        problems.append("destinations should include httpbin.org:443")
    tools = set(task.get("allowedTools", []))
    if not {"get_customer", "lookup_order", "issue_refund", "classify_ticket"} <= tools or "adjust_invoice" in tools:
        problems.append("tools should be classify_ticket, get_customer, lookup_order, issue_refund")
    if task.get("riskCeiling") != "medium":
        problems.append("risk ceiling should be medium")
    if task.get("readOnly"):
        problems.append("the task should not be read only")
    for p in problems:
        print(f"  warning: {p}; some results will differ from the expectations", flush=True)


def run_case(task_id, args):
    """Make one call and return (verdict, detail)."""
    args = dict(args)
    try:
        status, _ = call(args.pop("method"), args.pop("url"), task_id=task_id, **args)
    except Refused as e:
        return VERDICT_BY_CODE.get(e.code, e.code), f"{e.code} {e.decision_id}".strip()
    return "allow", f"forwarded, the API answered {status}"


def main():
    if not gateway_configured():
        print("not in a Trustabl sandbox: TRUSTABL_EGRESS_URL and TRUSTABL_EGRESS_TOKEN are not set", flush=True)
        return 2

    print(f"waiting up to {TASK_WAIT_SECONDS}s for an approved task in Trustabl...", flush=True)
    task = wait_for_task(TASK_WAIT_SECONDS)
    if not task:
        print("no task was approved in time; every call would only be held, so nothing was checked", flush=True)
        return 2
    print(f"task: {task.get('name', task['id'])}", flush=True)
    check_task(task)

    # Every call names the task, as an agent working on it would. That is what
    # makes the wire transfer a deny (host not in the task) rather than a
    # review (claim names no task).
    failed = 0
    for name, want, args in CASES:
        got, detail = run_case(task["id"], args)
        ok = got == want
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:28} expected {want:6} got {got:6} ({detail})", flush=True)

    print(f"\n{len(CASES) - failed} of {len(CASES)} calls got the expected verdict.", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
