from app.model import Invoice
from app.services.errors import Conflict


def require_draft(invoice: Invoice, action: str) -> None:
    """Invoices, and their documents, can only change while they are drafts."""
    if invoice.status != "Draft":
        raise Conflict(f"Only draft invoices can be {action}.")
