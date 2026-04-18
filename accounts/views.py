"""Auth views: login, logout, password reset (request + confirm), profile, password change."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from .forms import (
    LoginForm,
    PasswordChangeForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    ProfileForm,
)
from .models import LoginAttempt, PasswordResetToken
from .permissions import has_any_role


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    form = LoginForm(request=request, data=request.POST or None)
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        ip = _client_ip(request)
        ua = request.META.get("HTTP_USER_AGENT", "")[:512]
        if form.is_valid():
            user = form.user
            auth_login(request, user)
            user.last_login_ip = ip
            user.last_login_at = timezone.now()
            user.save(update_fields=["last_login_ip", "last_login_at"])
            LoginAttempt.objects.create(email=email, ip_address=ip, user_agent=ua, success=True)
            if user.force_password_change:
                messages.warning(request, "You must change your password before continuing.")
                return redirect("accounts:password-change")
            return redirect(request.GET.get("next") or settings.LOGIN_REDIRECT_URL)
        LoginAttempt.objects.create(
            email=email, ip_address=ip, user_agent=ua, success=False, failure_reason="invalid_login"
        )
    return render(request, "accounts/login.html", {"form": form, "page_title": "Sign in"})


@require_http_methods(["POST", "GET"])
def logout_view(request):
    auth_logout(request)
    messages.info(request, "You have been signed out.")
    return redirect("accounts:login")


@require_http_methods(["GET", "POST"])
def password_reset_request(request):
    form = PasswordResetRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        from django.contrib.auth import get_user_model

        User = get_user_model()
        email = form.cleaned_data["email"].strip().lower()
        user = User.objects.filter(email__iexact=email).first()
        if user:
            token = PasswordResetToken.objects.create(user=user)
            # TODO: email dispatch via Celery (Phase 3.x). Log for now.
            messages.info(
                request,
                f"If that account exists, a reset link has been sent. (Dev token: {token.token})",
            )
        else:
            messages.info(request, "If that account exists, a reset link has been sent.")
        return redirect("accounts:login")
    return render(
        request,
        "accounts/password_reset_request.html",
        {"form": form, "page_title": "Reset password"},
    )


@require_http_methods(["GET", "POST"])
def password_reset_confirm(request, token):
    pr = get_object_or_404(PasswordResetToken, token=token)
    if not pr.is_valid():
        messages.error(request, "This reset link is invalid or has expired.")
        return redirect("accounts:password-reset")

    form = PasswordResetConfirmForm(pr.user, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        pr.user.set_password(form.cleaned_data["new_password1"])
        pr.user.force_password_change = False
        pr.user.save()
        pr.used_at = timezone.now()
        pr.save(update_fields=["used_at"])
        messages.success(request, "Password updated. Please sign in.")
        return redirect("accounts:login")
    return render(
        request,
        "accounts/password_reset_confirm.html",
        {"form": form, "page_title": "Set new password"},
    )


@login_required
@require_http_methods(["GET", "POST"])
def password_change(request):
    form = PasswordChangeForm(request.user, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        request.user.set_password(form.cleaned_data["new_password1"])
        request.user.force_password_change = False
        request.user.save()
        messages.success(request, "Password changed. Please sign in again.")
        auth_logout(request)
        return redirect("accounts:login")
    return render(
        request,
        "accounts/password_change.html",
        {"form": form, "page_title": "Change password"},
    )


@login_required
@require_http_methods(["GET", "POST"])
def profile_view(request):
    """A user views/edits own profile.

    SPEC restriction: tenants and landlords cannot edit their own profile;
    only employees (any other role) can. Enforced server-side.
    """
    user = request.user
    role_names = set(user.active_role_names())
    blocked_roles = {"TENANT", "LANDLORD"}
    can_edit = bool(role_names - blocked_roles) or user.is_superuser

    if request.method == "POST":
        if not can_edit:
            messages.error(request, "Your account type cannot edit its own profile. Contact an administrator.")
            return redirect("accounts:profile")
        form = ProfileForm(request.POST, request.FILES, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated.")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=user)

    return render(
        request,
        "accounts/profile.html",
        {"form": form, "can_edit": can_edit, "page_title": "My profile"},
    )
