"""Custom money fields — UGX and USD.

UGX is stored as whole numbers only — decimals rejected at save.
USD is stored with exactly 2 decimal places — rounded at save.

Never use a generic DecimalField for money. Use these fields.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import models


class UGXField(models.DecimalField):
    """Ugandan Shillings — whole numbers only.

    Stored as DECIMAL(14, 0) to remain compatible with mixed-currency
    aggregation at the DB level, but any fractional input is rejected.
    """

    description = "UGX amount (whole shillings)"

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", 14)
        kwargs["decimal_places"] = 0
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs.pop("decimal_places", None)
        kwargs.pop("max_digits", None)
        return name, path, args, kwargs

    def to_python(self, value):
        if value is None or value == "":
            return None
        try:
            d = Decimal(value) if not isinstance(value, Decimal) else value
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError("Invalid UGX amount")
        if d != d.to_integral_value():
            raise ValidationError("UGX amounts must be whole numbers (no decimals).")
        return d.to_integral_value()

    def get_prep_value(self, value):
        value = self.to_python(value)
        return super().get_prep_value(value)


class USDField(models.DecimalField):
    """United States Dollars — exactly 2 decimal places.

    Inputs with more than 2 decimals are rounded half-up to 2 dp on save.
    """

    description = "USD amount (2 decimal places)"

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", 12)
        kwargs["decimal_places"] = 2
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs.pop("decimal_places", None)
        kwargs.pop("max_digits", None)
        return name, path, args, kwargs

    def to_python(self, value):
        if value is None or value == "":
            return None
        try:
            d = Decimal(value) if not isinstance(value, Decimal) else value
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError("Invalid USD amount")
        return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def get_prep_value(self, value):
        value = self.to_python(value)
        return super().get_prep_value(value)
