import uuid

from flask import current_app, request

from app.database import db
from app.model import Vendor
from app.services.errors import Unauthorized


def get_current_vendor() -> Vendor:
    """The vendor making the request.

    Placeholder until authentication exists: the vendor id comes from the X-Vendor-Id header,
    falling back to DEFAULT_VENDOR_ID for local development. Replace with the vendor on the
    authenticated user's session/token.
    """
    raw_id = request.headers.get("X-Vendor-Id") or current_app.config.get("DEFAULT_VENDOR_ID")
    if not raw_id:
        raise Unauthorized("Missing X-Vendor-Id header.")
    try:
        vendor_id = uuid.UUID(raw_id)
    except ValueError:
        raise Unauthorized("Invalid X-Vendor-Id header.")

    vendor = db.session.get(Vendor, vendor_id)
    if vendor is None or not vendor.is_active:
        raise Unauthorized("Unknown or inactive vendor.")
    return vendor
