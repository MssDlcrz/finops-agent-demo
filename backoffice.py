"""A small finance back-office: three agents built on the OpenAI Agents SDK.

This file is the reviewed source of truth. A Trustabl scan reads it and
compiles the contract: which agents exist and which tools each may use.

  - TriageAgent   classifies a ticket and loads the customer.
  - RefundAgent   may look up an order and refund it to the original payment
                  method. Nothing else.
  - BillingAgent  takes its tools from a registry at start-up, so its tool list
                  cannot be read from the code.
"""

from agents import Agent, function_tool


@function_tool
def classify_ticket(text: str = "") -> str:
    """Classify a customer ticket into a routing category."""
    return "category: refund"


@function_tool
def get_customer(customer_id: str = "") -> str:
    """Fetch a customer profile by id."""
    return f"Customer {customer_id or 'unknown'}: tier gold, 3 open orders."


@function_tool
def lookup_order(order_id: str = "") -> str:
    """Look up an order by id."""
    return f"Order {order_id or 'unknown'}: delivered, $5,000.00, card on file."


@function_tool
def issue_refund(order_id: str = "", amount: str = "") -> str:
    """Refund an order to the customer's original payment method."""
    return f"Refunded {amount or 'the amount'} for order {order_id or 'unknown'} to the original card."


@function_tool
def adjust_invoice(invoice_id: str = "", amount: str = "") -> str:
    """Adjust an invoice amount."""
    return f"Adjusted invoice {invoice_id or 'unknown'} by {amount or 'the amount'}."


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
