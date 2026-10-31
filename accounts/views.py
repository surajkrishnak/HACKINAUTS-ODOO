import datetime
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import (
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    ChangePasswordForm,
    CustomerSignupForm,
    LoginForm,
    ProfileUpdateForm,
    StaffCreationForm,
)
from .models import User
from .permissions import role_required


def redirect_user_by_role(user):
    """Redirects authenticated users based on their assigned role."""
    if user.is_customer:
        return redirect("/portal/")
    # Admin and Staff go to dashboard
    return redirect("/")


def signup_view(request):
    """Customer registration view. Automatically logs in and redirects to customer portal."""
    if request.user.is_authenticated:
        return redirect_user_by_role(request.user)

    if request.method == "POST":
        form = CustomerSignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f"Welcome to Brightloop, {user.first_name or user.email}! Your customer account is active.")
            return redirect_user_by_role(user)
    else:
        form = CustomerSignupForm()

    return render(request, "accounts/signup.html", {"form": form})


def login_view(request):
    """
    User login view with email-based authentication and brute-force lockout protection:
    Locks account for 5 minutes after 5 consecutive failed attempts.
    """
    if request.user.is_authenticated:
        return redirect_user_by_role(request.user)

    form = LoginForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        password = form.cleaned_data["password"]
        now = timezone.now()

        # Check if user exists to enforce brute-force locks
        user_candidate = User.objects.filter(email__iexact=email).first()

        if user_candidate and user_candidate.locked_until:
            if user_candidate.locked_until > now:
                remaining_seconds = int((user_candidate.locked_until - now).total_seconds())
                remaining_mins = max(1, (remaining_seconds + 59) // 60)
                messages.error(
                    request,
                    f"Account is temporarily locked due to repeated failed login attempts. "
                    f"Please try again in {remaining_mins} minute(s).",
                )
                return render(request, "accounts/login.html", {"form": form})
            else:
                # Lockout period expired; reset lock
                user_candidate.locked_until = None
                user_candidate.failed_attempts = 0
                user_candidate.save(update_fields=["locked_until", "failed_attempts"])

        # Authenticate user
        user = authenticate(request, username=email, password=password)

        if user is not None:
            if not user.is_active:
                messages.error(request, "This account is inactive. Please contact support.")
                return render(request, "accounts/login.html", {"form": form})

            # Successful login: reset attempts and lock
            if user.failed_attempts > 0 or user.locked_until is not None:
                user.failed_attempts = 0
                user.locked_until = None
                user.save(update_fields=["failed_attempts", "locked_until"])

            login(request, user)
            messages.success(request, f"Welcome back, {user.first_name or user.email}!")

            # Support ?next= redirection safely
            next_url = request.GET.get("next")
            if next_url and next_url.startswith("/"):
                return redirect(next_url)
            return redirect_user_by_role(user)
        else:
            # Failed attempt: record against user candidate if exists
            if user_candidate:
                user_candidate.failed_attempts += 1
                if user_candidate.failed_attempts >= 5:
                    user_candidate.locked_until = now + datetime.timedelta(minutes=5)
                    messages.error(
                        request,
                        "Maximum login attempts exceeded. Your account has been locked for 5 minutes.",
                    )
                else:
                    attempts_left = 5 - user_candidate.failed_attempts
                    messages.error(
                        request,
                        f"Invalid email or password. You have {attempts_left} attempt(s) remaining before temporary lockout.",
                    )
                user_candidate.save(update_fields=["failed_attempts", "locked_until"])
            else:
                messages.error(request, "Invalid email or password.")

    return render(request, "accounts/login.html", {"form": form})


@require_POST
def logout_view(request):
    """Logs out user via POST request and redirects to login."""
    logout(request)
    messages.info(request, "You have been safely logged out.")
    return redirect("/accounts/login/")


@login_required
def profile_view(request):
    """Allows logged-in users to update their profile details or change password."""
    user = request.user
    profile_form = ProfileUpdateForm(instance=user)
    password_form = ChangePasswordForm(user=user)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update_profile":
            profile_form = ProfileUpdateForm(request.POST, instance=user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Your profile details have been updated.")
                return redirect("/accounts/profile/")
        elif action == "change_password":
            password_form = ChangePasswordForm(user, request.POST)
            if password_form.is_valid():
                new_password = password_form.cleaned_data["new_password"]
                user.set_password(new_password)
                user.save()
                update_session_auth_hash(request, user)  # Prevent logout on password change
                messages.success(request, "Your password has been successfully changed.")
                return redirect("/accounts/profile/")

    return render(request, "accounts/profile.html", {
        "profile_form": profile_form,
        "password_form": password_form,
    })


@role_required("admin")
def staff_management_view(request):
    """
    Admin-only staff management:
    Lists current staff members and provisions new staff users.
    Staff and customers attempting access will receive HTTP 403 Forbidden.
    """
    staff_users = User.objects.filter(role=User.Role.STAFF).order_by("-date_joined")
    form = StaffCreationForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        new_staff = form.save()
        messages.success(request, f"Staff member {new_staff.get_full_name()} ({new_staff.email}) created successfully.")
        return redirect("/accounts/staff/")

    return render(request, "accounts/staff_list.html", {
        "staff_users": staff_users,
        "form": form,
    })


# Password Reset Views with custom Bootstrap templates and 1-hour expiry
class CustomPasswordResetView(PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/password_reset_email.html"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")


class CustomPasswordResetDoneView(PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class CustomPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("password_reset_complete")


class CustomPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"
