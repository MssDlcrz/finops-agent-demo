"""A small finance back-office: three agents built on the OpenAI Agents SDK.

This file is the reviewed source of truth. A Trustabl scan reads it and
compiles the contract: which agents exist and which tools each may use.

  - TriageAgent   classifies a ticket and loads the customer.
  - RefundAgent   may look up an order and refund it to the original payment
                  method. Nothing else.
  - BillingAgent  takes its tools from a registry at start-up, so its tool list
                  cannot be read from the code.
"""

import os
import re

from agents import Agent, function_tool

from trustabl_egress import Refused, call

# The back-office API every tool calls. httpbin.org echoes each request back,
# so the calls are real HTTPS calls with nothing to set up; point this at a
# real API to use one.
BACKOFFICE_API_URL = os.environ.get("BACKOFFICE_API_URL", "https://httpbin.org/anything/backoffice").rstrip("/")


def backoffice(method, path, *, tool, operation, risk, payload=None):
    """Call the back-office API and say what happened, in words the model reads.

    In a Trustabl sandbox the call goes through the egress gateway with a claim
    (see trustabl_egress.py). A refused call is reported to the model rather
    than raised, so the run carries on to the next ticket.
    """
    try:
        status, _ = call(method, f"{BACKOFFICE_API_URL}{path}", tool=tool, operation=operation, risk=risk, json_body=payload)
    except Refused as e:
        print(f"    {tool}: refused by Trustabl ({e.code})", flush=True)
        if e.code == "review_pending":
            return "This action is waiting for a person to approve it in Trustabl. It has not run."
        return f"Trustabl refused this action ({e.code}). It has not run."
    print(f"    {tool}: {method} {path} -> {status}", flush=True)
    if status >= 400:
        return f"The back office answered {status}. The action did not complete."
    return None


def _dollars(amount):
    digits = re.sub(r"[^0-9.]", "", amount or "")
    try:
        return float(digits)
    except ValueError:
        return 0.0


@function_tool
def classify_ticket(text: str = "") -> str:
    """Classify a customer ticket into a routing category."""
    failed = backoffice("POST", "/tickets/classify", tool="classify_ticket", operation="read", risk="low", payload={"text": text})
    return failed or "category: refund"


@function_tool
def get_customer(customer_id: str = "") -> str:
    """Fetch a customer profile by id."""
    failed = backoffice("GET", f"/customers/{customer_id or 'unknown'}", tool="get_customer", operation="read", risk="low")
    return failed or f"Customer {customer_id or 'unknown'}: tier gold, 3 open orders."


@function_tool
def lookup_order(order_id: str = "") -> str:
    """Look up an order by id."""
    failed = backoffice("GET", f"/orders/{order_id or 'unknown'}", tool="lookup_order", operation="read", risk="low")
    return failed or f"Order {order_id or 'unknown'}: delivered, $5,000.00, card on file."


@function_tool
def issue_refund(order_id: str = "", amount: str = "") -> str:
    """Refund an order to the customer's original payment method."""
    # A large refund is declared high risk, so Trustabl holds it for a person.
    risk = "high" if _dollars(amount) >= 1000 else "medium"
    failed = backoffice(
        "POST", f"/orders/{order_id or 'unknown'}/refunds",
        tool="issue_refund", operation="write", risk=risk, payload={"amount": amount},
    )
    return failed or f"Refunded {amount or 'the amount'} for order {order_id or 'unknown'} to the original card."


@function_tool
def adjust_invoice(invoice_id: str = "", amount: str = "") -> str:
    """Adjust an invoice amount."""
    failed = backoffice(
        "POST", f"/invoices/{invoice_id or 'unknown'}/adjustments",
        tool="adjust_invoice", operation="write", risk="medium", payload={"amount": amount},
    )
    return failed or f"Adjusted invoice {invoice_id or 'unknown'} by {amount or 'the amount'}."


def billing_toolset():
    """Billing tools come from a feature-flagged registry at start-up."""
    return [adjust_invoice]


triage_agent = Agent(
    name="TriageAgent",
    instructions="You triage customer tickets. Call a tool; do not reply with plain text.",
    tools=[classify_ticket, get_customer],
)

refund_agent = Agent(
    name="RefundAgent",
    instructions=(
        "You process refunds. Do what the ticket asks by calling the matching tool. "
        "Do not reply with plain text."
    ),
    tools=[lookup_order, issue_refund],
)

billing_agent = Agent(
    name="BillingAgent",
    instructions="You adjust invoices. Call a tool; do not reply with plain text.",
    tools=billing_toolset(),
)
