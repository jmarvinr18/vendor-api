from datetime import timezone
from decimal import ROUND_HALF_UP

from marshmallow import fields


class Money(fields.Decimal):
    """Loads as a 2-place Decimal, dumps as a JSON number (the UI works with numbers)."""

    def __init__(self, **kwargs):
        super().__init__(places=2, rounding=ROUND_HALF_UP, allow_nan=False, **kwargs)

    def _serialize(self, value, attr, obj, **kwargs):
        return None if value is None else float(value)

    def _deserialize(self, value, attr, data, **kwargs):
        # Go through str so floats like 0.1 don't pick up binary noise.
        if isinstance(value, float):
            value = str(value)
        return super()._deserialize(value, attr, data, **kwargs)


class UtcDateTime(fields.DateTime):
    """Always dumps with a UTC offset, even when the database hands back a naive value."""

    def _serialize(self, value, attr, obj, **kwargs):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return super()._serialize(value, attr, obj, **kwargs)
