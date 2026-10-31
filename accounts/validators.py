import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


class ComplexityPasswordValidator:
    """
    Validates that passwords meet Brightloop security requirements:
    - More than 8 characters (minimum 9 characters)
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one special character
    """

    def validate(self, password, user=None):
        if len(password) <= 8:
            raise ValidationError(
                _("Password must be more than 8 characters long (minimum 9 characters)."),
                code="password_too_short",
            )
        if not re.search(r"[A-Z]", password):
            raise ValidationError(
                _("Password must contain at least one uppercase letter (A-Z)."),
                code="password_no_upper",
            )
        if not re.search(r"[a-z]", password):
            raise ValidationError(
                _("Password must contain at least one lowercase letter (a-z)."),
                code="password_no_lower",
            )
        # Check for any character that is not alphanumeric or whitespace
        if not re.search(r"[^a-zA-Z0-9\s]", password):
            raise ValidationError(
                _("Password must contain at least one special character (e.g. !@#$%^&*)."),
                code="password_no_special",
            )

    def get_help_text(self):
        return _(
            "Your password must be at least 9 characters long and contain at least one uppercase letter, one lowercase letter, and one special character."
        )
