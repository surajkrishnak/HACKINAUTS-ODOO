import datetime
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlsafe_base64_encode

from accounts.validators import ComplexityPasswordValidator

User = get_user_model()


class PasswordValidatorTests(TestCase):
    """Test custom password complexity validator against individual rules."""

    def setUp(self):
        self.validator = ComplexityPasswordValidator()

    def test_password_too_short(self):
        """Passwords with 8 or fewer characters must be rejected."""
        with self.assertRaises(ValidationError) as ctx:
            self.validator.validate("Pass@123")  # 8 chars
        self.assertEqual(ctx.exception.code, "password_too_short")

    def test_password_missing_uppercase(self):
        """Passwords without uppercase letters must be rejected."""
        with self.assertRaises(ValidationError) as ctx:
            self.validator.validate("password@123")
        self.assertEqual(ctx.exception.code, "password_no_upper")

    def test_password_missing_lowercase(self):
        """Passwords without lowercase letters must be rejected."""
        with self.assertRaises(ValidationError) as ctx:
            self.validator.validate("PASSWORD@123")
        self.assertEqual(ctx.exception.code, "password_no_lower")

    def test_password_missing_special_character(self):
        """Passwords without special characters must be rejected."""
        with self.assertRaises(ValidationError) as ctx:
            self.validator.validate("Password123")
        self.assertEqual(ctx.exception.code, "password_no_special")

    def test_valid_strong_password(self):
        """Passwords meeting all 4 criteria must pass validation."""
        try:
            self.validator.validate("Password@123")  # 12 chars, upper, lower, special
        except ValidationError:
            self.fail("Valid strong password raised ValidationError unexpectedly!")


class AuthAndSignupTests(TestCase):
    """Test customer signup, unique email enforcement, and role assignment."""

    def test_customer_signup_success(self):
        response = self.client.post(
            reverse("signup"),
            {
                "full_name": "Alice Customer",
                "email": "alice@customer.com",
                "phone": "+1 555 1234",
                "password": "Password@123",
                "confirm_password": "Password@123",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="alice@customer.com")
        self.assertEqual(user.role, User.Role.CUSTOMER)
        self.assertEqual(user.first_name, "Alice")
        self.assertEqual(user.last_name, "Customer")
        # Confirms user is logged in
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        # Redirected to customer portal
        self.assertRedirects(response, "/portal/")

    def test_duplicate_email_rejected_case_insensitive(self):
        """Duplicate emails must be rejected with an inline validation error."""
        User.objects.create_user(email="alice@customer.com", password="Password@123")

        response = self.client.post(
            reverse("signup"),
            {
                "full_name": "Alice Duplicate",
                "email": "ALICE@CUSTOMER.COM",  # uppercase check
                "password": "Password@123",
                "confirm_password": "Password@123",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "email",
            "An account with this email address already exists. Please login instead.",
        )


class LoginAndLockoutTests(TestCase):
    """Test login redirection by role and brute-force lockout protections."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            email="admin@brightloop.test",
            password="Password@123",
            role=User.Role.ADMIN,
        )
        self.staff_user = User.objects.create_user(
            email="staff@brightloop.test",
            password="Password@123",
            role=User.Role.STAFF,
            is_staff=True,
        )
        self.customer_user = User.objects.create_user(
            email="cust@brightloop.test",
            password="Password@123",
            role=User.Role.CUSTOMER,
        )

    def test_login_redirect_by_role(self):
        # Admin -> Dashboard
        resp_admin = self.client.post(
            reverse("login"),
            {"email": "admin@brightloop.test", "password": "Password@123"},
        )
        self.assertRedirects(resp_admin, "/")
        self.client.logout()

        # Staff -> Dashboard
        resp_staff = self.client.post(
            reverse("login"),
            {"email": "staff@brightloop.test", "password": "Password@123"},
        )
        self.assertRedirects(resp_staff, "/")
        self.client.logout()

        # Customer -> Portal
        resp_cust = self.client.post(
            reverse("login"),
            {"email": "cust@brightloop.test", "password": "Password@123"},
        )
        self.assertRedirects(resp_cust, "/portal/")

    def test_brute_force_lockout_after_5_failures(self):
        """5 consecutive bad passwords must lock user for 5 minutes."""
        for attempt in range(1, 6):
            self.client.post(
                reverse("login"),
                {"email": "cust@brightloop.test", "password": "WrongPassword@999"},
            )

        self.customer_user.refresh_from_db()
        self.assertEqual(self.customer_user.failed_attempts, 5)
        self.assertIsNotNone(self.customer_user.locked_until)
        self.assertGreater(self.customer_user.locked_until, timezone.now())

        # 6th attempt even with correct password is blocked while locked
        response = self.client.post(
            reverse("login"),
            {"email": "cust@brightloop.test", "password": "Password@123"},
        )
        self.assertContains(response, "Account is temporarily locked")


class StaffManagementPermissionsTests(TestCase):
    """Test admin-only staff management and 403 Forbidden enforcement."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            email="admin@brightloop.test",
            password="Password@123",
            role=User.Role.ADMIN,
        )
        self.staff_user = User.objects.create_user(
            email="staff@brightloop.test",
            password="Password@123",
            role=User.Role.STAFF,
            is_staff=True,
        )
        self.customer_user = User.objects.create_user(
            email="customer@brightloop.test",
            password="Password@123",
            role=User.Role.CUSTOMER,
        )

    def test_customer_cannot_open_staff_page(self):
        """Customer accessing /accounts/staff/ must get HTTP 403 Forbidden."""
        self.client.force_login(self.customer_user)
        response = self.client.get(reverse("staff_management"))
        self.assertEqual(response.status_code, 403)

    def test_staff_cannot_open_staff_page(self):
        """Staff member accessing /accounts/staff/ must get HTTP 403 Forbidden."""
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("staff_management"))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_staff(self):
        """Admin can access staff page and provision new staff member."""
        self.client.force_login(self.admin_user)
        get_resp = self.client.get(reverse("staff_management"))
        self.assertEqual(get_resp.status_code, 200)

        post_resp = self.client.post(
            reverse("staff_management"),
            {
                "first_name": "Bob",
                "last_name": "Engineer",
                "email": "bob@brightloop.test",
                "phone": "+91 9988776655",
                "temporary_password": "Password@123",
            },
            follow=True,
        )
        self.assertEqual(post_resp.status_code, 200)
        bob = User.objects.get(email="bob@brightloop.test")
        self.assertEqual(bob.role, User.Role.STAFF)
        self.assertTrue(bob.is_staff)


class PasswordResetTests(TestCase):
    """Test password reset email generation, token usage, and single-use invalidation."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="forgotten@brightloop.test",
            password="OldPassword@123",
        )

    def test_password_reset_flow_and_single_use_token(self):
        # 1. Request reset link
        response = self.client.post(
            reverse("password_reset"),
            {"email": "forgotten@brightloop.test"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

        # 2. Extract token and uidb64
        uid = urlsafe_base64_encode(str(self.user.pk).encode())
        token = default_token_generator.make_token(self.user)

        confirm_url = reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token})

        # 3. Open reset confirm page (Django redirects from token to set-password to avoid referer leakage)
        resp_confirm_get = self.client.get(confirm_url, follow=True)
        self.assertEqual(resp_confirm_get.status_code, 200)
        self.assertTrue(resp_confirm_get.context["validlink"])

        # 4. Set new password by posting to the set-password endpoint
        set_pwd_url = resp_confirm_get.redirect_chain[-1][0]
        resp_confirm_post = self.client.post(
            set_pwd_url,
            {
                "new_password1": "NewStrongPass@2026",
                "new_password2": "NewStrongPass@2026",
            },
            follow=True,
        )
        self.assertEqual(resp_confirm_post.status_code, 200)

        # Verify password changed
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewStrongPass@2026"))

        # 5. Try reusing the exact same reset link - must be invalid
        resp_reuse = self.client.get(confirm_url, follow=True)
        self.assertEqual(resp_reuse.status_code, 200)
        self.assertFalse(resp_reuse.context["validlink"])
        self.assertContains(resp_reuse, "Invalid or Expired Link")
