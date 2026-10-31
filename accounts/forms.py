from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone

User = get_user_model()


class CustomerSignupForm(forms.Form):
    """Signup form for new customers with password rules and unique email validation."""

    full_name = forms.CharField(
        max_length=150,
        label="Full Name",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g. Jane Doe",
            "autofocus": True,
        }),
    )
    email = forms.EmailField(
        label="Email Address",
        widget=forms.EmailInput(attrs={
            "class": "form-control",
            "placeholder": "jane@example.com",
        }),
    )
    phone = forms.CharField(
        max_length=30,
        required=False,
        label="Phone Number (Optional)",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "+1 (555) 000-0000",
        }),
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Enter strong password",
            "id": "id_password",
        }),
        help_text="Must be at least 9 characters, with at least 1 uppercase letter, 1 lowercase letter, and 1 special character.",
    )
    confirm_password = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Re-enter password",
        }),
    )

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email address already exists. Please login instead.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirm_password = cleaned_data.get("confirm_password")

        if password and confirm_password:
            if password != confirm_password:
                self.add_error("confirm_password", "Passwords do not match.")
            else:
                # Run full password validators (including ComplexityPasswordValidator)
                validate_password(password)
        return cleaned_data

    def save(self):
        email = self.cleaned_data["email"]
        password = self.cleaned_data["password"]
        full_name = self.cleaned_data.get("full_name", "").strip()
        parts = full_name.split(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""
        phone = self.cleaned_data.get("phone", "")

        user = User.objects.create_user(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            role=User.Role.CUSTOMER,
        )
        return user


class LoginForm(forms.Form):
    """Email + password login form with brute-force lockout checking."""

    email = forms.EmailField(
        label="Email Address",
        widget=forms.EmailInput(attrs={
            "class": "form-control",
            "placeholder": "name@example.com",
            "autofocus": True,
        }),
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Password",
        }),
    )


class StaffCreationForm(forms.Form):
    """Admin-only form to provision new staff members."""

    first_name = forms.CharField(
        max_length=150,
        label="First Name",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "First name"}),
    )
    last_name = forms.CharField(
        max_length=150,
        label="Last Name",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Last name"}),
    )
    email = forms.EmailField(
        label="Work Email Address",
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "staff@brightloop.test"}),
    )
    phone = forms.CharField(
        max_length=30,
        required=False,
        label="Phone Number",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "+91 ..."}),
    )
    temporary_password = forms.CharField(
        label="Temporary Password",
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Initial secure password"}),
        help_text="Must be at least 9 characters, with at least 1 uppercase letter, 1 lowercase letter, and 1 special character.",
    )

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("A user with this email address already exists.")
        return email

    def clean_temporary_password(self):
        password = self.cleaned_data.get("temporary_password")
        if password:
            validate_password(password)
        return password

    def save(self):
        first_name = self.cleaned_data["first_name"]
        last_name = self.cleaned_data["last_name"]
        email = self.cleaned_data["email"]
        phone = self.cleaned_data.get("phone", "")
        password = self.cleaned_data["temporary_password"]

        user = User.objects.create_user(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            role=User.Role.STAFF,
            is_staff=True,
        )
        return user


class ProfileUpdateForm(forms.ModelForm):
    """Form allowing users to update their name and contact info."""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "phone"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
        }


class ChangePasswordForm(forms.Form):
    """Form allowing users to change their account password."""

    old_password = forms.CharField(
        label="Current Password",
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Current password"}),
    )
    new_password = forms.CharField(
        label="New Password",
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "New password"}),
        help_text="At least 9 characters with 1 uppercase, 1 lowercase, and 1 special character.",
    )
    confirm_new_password = forms.CharField(
        label="Confirm New Password",
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Re-type new password"}),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_old_password(self):
        old_password = self.cleaned_data.get("old_password")
        if not self.user.check_password(old_password):
            raise ValidationError("Your current password was entered incorrectly.")
        return old_password

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get("new_password")
        confirm = cleaned_data.get("confirm_new_password")

        if new_password and confirm:
            if new_password != confirm:
                self.add_error("confirm_new_password", "New passwords do not match.")
            else:
                validate_password(new_password, user=self.user)
        return cleaned_data
