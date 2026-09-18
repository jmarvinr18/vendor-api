import uuid

from flask import current_app, request
from flask_smorest import abort

from app.database import db
from app.model import Vendor


def get_current_vendor() -> Vendor:
    """The vendor making the request.

    Placeholder until authentication exists: the vendor id comes from the X-Vendor-Id header,
    falling back to DEFAULT_VENDOR_ID for local development. Replace with the vendor on the
    authenticated user's session/token.
    """
    raw_id = request.headers.get("X-Vendor-Id") or current_app.config.get("DEFAULT_VENDOR_ID")
    if not raw_id:
        abort(401, message="Missing X-Vendor-Id header.")
    try:
        vendor_id = uuid.UUID(raw_id)
    except ValueError:
        abort(401, message="Invalid X-Vendor-Id header.")

    vendor = db.session.get(Vendor, vendor_id)
    if vendor is None or not vendor.is_active:
        abort(401, message="Unknown or inactive vendor.")
    return vendor
