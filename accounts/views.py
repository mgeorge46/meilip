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
from .models import AuditAction, AuditLog, LoginAttempt, PasswordResetToken
from .permissions import has_any_role, role_required


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def landing_for(user):
    """Pick the right post-login destination for a user based on their roles.

    Tenants land in /tenant/, landlords in /landlord/, everyone else (staff,
    finance, admin, etc.) lands on the operations dashboard at /.
    Superusers always go to /.
    """
    if not user.is_authenticated:
        return settings.LOGIN_URL
    if user.is_superuser:
        return "/"
    role_names = set(user.active_role_names() or ())
    staff_roles = {"SUPER_ADMIN", "ADMIN", "ACCOUNT_MANAGER", "COLLECTIONS",
                   "SALES_REP", "FINANCE"}
    if role_names & staff_roles:
        return "/"
    if "TENANT" in role_names:
        return "/tenant/"
    if "LANDLORD" in role_names:
        return "/landlord/"
    return settings.LOGIN_REDIRECT_URL


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect(landing_for(request.user))

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
            # Honour ?next= if it's safe; otherwise route by role.
            return redirect(request.GET.get("next") or landing_for(user))
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
        if user and user.is_active:
            token = PasswordResetToken.objects.create(user=user)
            _send_password_reset_email(request, user, token)
        # Always show the same message regardless — never leak whether the
        # email exists.
        messages.info(
            request,
            "If that account exists, a reset link has been sent. "
            "Check your inbox (and the delivery log if it doesn't arrive)."
        )
        return redirect("accounts:login")
    return render(
        request,
        "accounts/password_reset_request.html",
        {"form": form, "page_title": "Reset password"},
    )


def _send_password_reset_email(request, user, token):
    """Enqueue a password-reset email through the notifications pipeline.

    Falls back gracefully if the broker is down — the NotificationDelivery
    row stays QUEUED and the scheduled sweeper retries.
    """
    from django.urls import reverse as _reverse
    from notifications.models import Channel, Template
    from notifications.services import enqueue_notification

    reset_path = _reverse("accounts:password-reset-confirm", args=[token.token])
    reset_url = request.build_absolute_uri(reset_path)
    seconds_left = max(60, int((token.expires_at - timezone.now()).total_seconds()))
    if seconds_left >= 3600:
        expires_in = f"{seconds_left // 3600} hour{'s' if seconds_left // 3600 != 1 else ''}"
    else:
        expires_in = f"{max(1, seconds_left // 60)} minutes"

    enqueue_notification(
        template=Template.PASSWORD_RESET,
        channel=Channel.EMAIL,
        recipient=user.email,
        context={
            "user_name": user.get_full_name() or user.email,
            "email": user.email,
            "reset_url": reset_url,
            "expires_in": expires_in,
            "expiry_hours": expires_in,  # backwards-compat key for any existing template
            "expires_at": token.expires_at.isoformat(),
        },
        user=user,
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


# ---------------------------------------------------------------------------
# Audit log viewer — Admin / Super-Admin only.
# Filters: actor email, action, target type, date range.
# ---------------------------------------------------------------------------
@role_required("ADMIN", "SUPER_ADMIN")
@require_GET
def audit_log_view(request):
    from django.core.paginator import Paginator

    qs = AuditLog.objects.select_related("actor").all()

    actor_q = (request.GET.get("actor") or "").strip()
    action = (request.GET.get("action") or "").strip()
    target_type = (request.GET.get("target_type") or "").strip()
    date_from = (request.GET.get("from") or "").strip()
    date_to = (request.GET.get("to") or "").strip()

    if actor_q:
        qs = qs.filter(actor__email__icontains=actor_q)
    if action:
        qs = qs.filter(action=action)
    if target_type:
        qs = qs.filter(target_type__iexact=target_type)
    if date_from:
        qs = qs.filter(timestamp__date__gte=date_from)
    if date_to:
        qs = qs.filter(timestamp__date__lte=date_to)

    page_size = int(request.GET.get("page_size") or settings.PAGINATION_DEFAULT)
    if page_size not in settings.PAGINATION_PAGE_SIZES:
        page_size = settings.PAGINATION_DEFAULT
    paginator = Paginator(qs, page_size)
    page = paginator.get_page(request.GET.get("page"))

    target_types = (
        AuditLog.objects.exclude(target_type="")
        .values_list("target_type", flat=True).distinct().order_by("target_type")
    )

    return render(
        request,
        "accounts/audit_log.html",
        {
            "page_title": "Audit log",
            "page": page,
            "action_choices": AuditAction.choices,
            "target_types": list(target_types),
            "filters": {
                "actor": actor_q, "action": action, "target_type": target_type,
                "from": date_from, "to": date_to, "page_size": page_size,
            },
            "page_sizes": settings.PAGINATION_PAGE_SIZES,
        },
    )


# ---------------------------------------------------------------------------
# User management — list, block, unblock (Phase G.3)
# ---------------------------------------------------------------------------
@role_required("ADMIN", "SUPER_ADMIN")
@require_GET
def user_list_view(request):
    """Paginated list of every User row with filters and search.

    Filters: status (active/blocked/all), role, free-text q (email, name).
    """
    from django.contrib.auth import get_user_model
    from django.core.paginator import Paginator
    from django.db.models import Count, Q

    User = get_user_model()
    qs = (
        User.objects.all()
        .annotate(role_count=Count("user_roles", filter=Q(user_roles__is_active=True)))
        .order_by("email")
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(email__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(phone__icontains=q)
        )
    status = (request.GET.get("status") or "all").strip()
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "blocked":
        qs = qs.filter(is_active=False)

    role = (request.GET.get("role") or "").strip()
    if role:
        qs = qs.filter(user_roles__role__name=role, user_roles__is_active=True).distinct()

    page_size = int(request.GET.get("page_size") or 50)
    if page_size not in [25, 50, 100, 200]:
        page_size = 50
    paginator = Paginator(qs, page_size)
    page = paginator.get_page(request.GET.get("page"))

    # Eager-load role names for the rendered rows
    visible = list(page.object_list)
    role_map = {}
    for ur in (
        __import__("accounts.models", fromlist=["UserRole"]).UserRole.objects
        .filter(user__in=visible, is_active=True)
        .select_related("role")
    ):
        role_map.setdefault(ur.user_id, []).append(ur.role.get_name_display())
    for u in visible:
        u.role_labels = ", ".join(sorted(role_map.get(u.pk, []))) or "—"

    from .models import Role
    return render(
        request,
        "accounts/user_list.html",
        {
            "page": page,
            "filter_q": q,
            "filter_status": status,
            "filter_role": role,
            "role_choices": Role.Name.choices,
            "page_size": page_size,
            "page_sizes": [25, 50, 100, 200],
            "page_title": "Users",
        },
    )


def _audit_user_action(actor, target, action: str, *, reason: str = "", request=None):
    """Append-only AuditLog row for a block/unblock action."""
    AuditLog.objects.create(
        actor=actor,
        action=AuditAction.UPDATE,
        target_type="User",
        target_id=str(target.pk),
        target_repr=target.email,
        path=getattr(request, "path", "") if request else "",
        method=getattr(request, "method", "") if request else "",
        ip_address=_client_ip(request) if request else None,
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:512] if request else ""),
        detail={"sub_action": action, "reason": reason},
    )


def _kill_user_sessions(user):
    """Force-log-out a blocked user by walking the session store and deleting
    every session that auth-references this user. Best-effort: returns the
    number of sessions deleted.
    """
    from django.contrib.sessions.models import Session
    from django.utils import timezone as _tz
    n = 0
    for s in Session.objects.filter(expire_date__gt=_tz.now()):
        try:
            data = s.get_decoded()
        except Exception:
            continue
        if str(data.get("_auth_user_id")) == str(user.pk):
            s.delete()
            n += 1
    return n


@role_required("ADMIN", "SUPER_ADMIN")
@require_http_methods(["POST"])
def user_block_view(request, pk):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    target = get_object_or_404(User, pk=pk)
    reason = (request.POST.get("reason") or "").strip()

    # Guard rails — never let an admin lock themselves or the last super admin
    if target.pk == request.user.pk:
        messages.error(request, "You cannot block your own account.")
        return redirect("accounts:user-list")
    if target.is_superuser:
        active_super = User.objects.filter(is_superuser=True, is_active=True).exclude(pk=target.pk).count()
        if active_super == 0:
            messages.error(request, "Cannot block the last active superuser.")
            return redirect("accounts:user-list")

    if not target.is_active:
        messages.info(request, f"{target.email} is already blocked.")
        return redirect("accounts:user-list")

    target.is_active = False
    target.save(update_fields=["is_active"])
    killed = _kill_user_sessions(target)
    _audit_user_action(request.user, target, "block", reason=reason, request=request)
    messages.success(
        request,
        f"Blocked {target.email}. {killed} active session(s) terminated."
    )
    return redirect("accounts:user-list")


@role_required("ADMIN", "SUPER_ADMIN")
@require_http_methods(["POST"])
def user_unblock_view(request, pk):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    target = get_object_or_404(User, pk=pk)
    if target.is_active:
        messages.info(request, f"{target.email} is already active.")
        return redirect("accounts:user-list")
    target.is_active = True
    target.save(update_fields=["is_active"])
    _audit_user_action(request.user, target, "unblock", request=request)
    messages.success(request, f"Unblocked {target.email}.")
    return redirect("accounts:user-list")


# ---------------------------------------------------------------------------
# Admin user CRUD + role assignment + password reset (Phase G.4)
# ---------------------------------------------------------------------------
def _generate_temp_password(n: int = 12) -> str:
    """Random URL-safe temporary password — always contains both a letter
    and a digit so default password validators pass."""
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(n))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def _sync_user_roles(user, role_names, *, actor):
    """Reconcile UserRole rows so the active set matches `role_names`."""
    from .models import Role, UserRole

    target = set(role_names or ())
    existing = {
        ur.role.name: ur
        for ur in user.user_roles.select_related("role").all()
    }
    for name in target:
        role = Role.objects.filter(name=name).first()
        if not role:
            continue
        ur = existing.get(name)
        if ur is None:
            UserRole.objects.create(user=user, role=role, assigned_by=actor, is_active=True)
        elif not ur.is_active:
            ur.is_active = True
            ur.assigned_by = actor
            ur.save(update_fields=["is_active", "assigned_by"])
    for name, ur in existing.items():
        if name not in target and ur.is_active:
            ur.is_active = False
            ur.save(update_fields=["is_active"])


def _kill_user_sessions_helper(user):
    from django.contrib.sessions.models import Session
    from django.utils import timezone as _tz
    n = 0
    for s in Session.objects.filter(expire_date__gt=_tz.now()):
        try:
            data = s.get_decoded()
        except Exception:
            continue
        if str(data.get("_auth_user_id")) == str(user.pk):
            s.delete()
            n += 1
    return n


@role_required("ADMIN", "SUPER_ADMIN")
@require_http_methods(["GET", "POST"])
def user_create_view(request):
    from django.contrib.auth import get_user_model
    from .forms import AdminUserCreateForm
    User = get_user_model()

    initial = {}
    bind_tenant = request.GET.get("tenant") or request.POST.get("bind_tenant")
    bind_landlord = request.GET.get("landlord") or request.POST.get("bind_landlord")
    if bind_tenant:
        from core.models import Tenant
        t = Tenant.objects.filter(pk=bind_tenant).first()
        if t:
            initial.update({
                "email": t.email or "",
                "phone": t.phone or "",
                "first_name": t.first_name,
                "last_name": t.last_name,
                "roles": ["TENANT"],
                "bind_tenant": t.pk,
            })
    if bind_landlord:
        from core.models import Landlord
        l = Landlord.objects.filter(pk=bind_landlord).first()
        if l:
            initial.update({
                "email": l.email or "",
                "phone": l.phone or "",
                "first_name": l.first_name,
                "last_name": l.last_name,
                "roles": ["LANDLORD"],
                "bind_landlord": l.pk,
            })

    if request.method == "POST":
        form = AdminUserCreateForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            password = data["password"] or _generate_temp_password()
            user = User(
                email=data["email"], phone=data["phone"],
                first_name=data["first_name"], last_name=data["last_name"],
                is_active=True, is_staff=False, is_superuser=False,
                force_password_change=bool(data.get("force_password_change", True)),
            )
            user.set_password(password)
            user.save()
            _sync_user_roles(user, data["roles"], actor=request.user)
            if data.get("bind_tenant"):
                from core.models import Tenant
                t = Tenant.objects.filter(pk=data["bind_tenant"]).first()
                if t and not t.user_id:
                    t.user = user
                    t.save(update_fields=["user"])
            if data.get("bind_landlord"):
                from core.models import Landlord
                l = Landlord.objects.filter(pk=data["bind_landlord"]).first()
                if l and not l.user_id:
                    l.user = user
                    l.save(update_fields=["user"])
            AuditLog.objects.create(
                actor=request.user, action=AuditAction.CREATE,
                target_type="User", target_id=str(user.pk),
                target_repr=user.email,
                ip_address=_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:512],
                detail={"roles": list(data["roles"])},
            )
            if not data["password"]:
                messages.success(
                    request,
                    f"User {user.email} created. Temporary password: {password} — "
                    f"share securely; user must change it on first login."
                )
            else:
                messages.success(request, f"User {user.email} created.")
            return redirect("accounts:user-detail", pk=user.pk)
    else:
        form = AdminUserCreateForm(initial=initial)

    return render(request, "accounts/user_form.html", {
        "form": form,
        "page_title": "New user",
        "is_create": True,
    })


@role_required("ADMIN", "SUPER_ADMIN")
@require_GET
def user_detail_view(request, pk):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    target = get_object_or_404(User, pk=pk)
    audit = AuditLog.objects.filter(
        target_type="User", target_id=str(target.pk),
    ).select_related("actor").order_by("-timestamp")[:25]
    return render(request, "accounts/user_detail.html", {
        "u": target,
        "role_labels": target.active_role_names(),
        "audit_entries": audit,
        "tenant_profile": getattr(target, "tenant_profile", None),
        "landlord_profile": getattr(target, "landlord_profile", None),
        "page_title": target.email,
    })


@role_required("ADMIN", "SUPER_ADMIN")
@require_http_methods(["GET", "POST"])
def user_edit_view(request, pk):
    from django.contrib.auth import get_user_model
    from .forms import AdminUserEditForm
    User = get_user_model()
    target = get_object_or_404(User, pk=pk)

    if request.method == "POST":
        form = AdminUserEditForm(request.POST, instance=target)
        if form.is_valid():
            user = form.save(commit=False)
            user.force_password_change = form.cleaned_data["force_password_change"]
            user.save()
            _sync_user_roles(user, form.cleaned_data["roles"], actor=request.user)
            AuditLog.objects.create(
                actor=request.user, action=AuditAction.UPDATE,
                target_type="User", target_id=str(user.pk), target_repr=user.email,
                ip_address=_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:512],
                detail={"sub_action": "edit", "roles": form.cleaned_data["roles"]},
            )
            messages.success(request, f"User {user.email} updated.")
            return redirect("accounts:user-detail", pk=user.pk)
    else:
        form = AdminUserEditForm(instance=target)

    return render(request, "accounts/user_form.html", {
        "form": form, "u": target, "page_title": f"Edit {target.email}",
        "is_create": False,
    })


@role_required("ADMIN", "SUPER_ADMIN")
@require_http_methods(["POST"])
def user_reset_password_view(request, pk):
    """Three modes — pick via POST `mode` field:

    • `inline` (default): admin sees the temp password in a flash message and
      shares it manually. Use when the user has no email or for offline.
    • `email_password`: emails a temp password to the user. Forces password
      change on next login.
    • `email_link`: emails a one-time reset *link* (token), not a password —
      preferred when the user has working email.

    All three: kill the user's active sessions, audit-log the event.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    target = get_object_or_404(User, pk=pk)
    mode = (request.POST.get("mode") or "inline").strip().lower()

    if mode == "email_link":
        if not target.email:
            messages.error(request, f"{target.email} has no email — cannot send a reset link.")
            return redirect("accounts:user-detail", pk=target.pk)
        token = PasswordResetToken.objects.create(user=target)
        _send_password_reset_email(request, target, token)
        n = _kill_user_sessions_helper(target)
        AuditLog.objects.create(
            actor=request.user, action=AuditAction.PASSWORD_RESET,
            target_type="User", target_id=str(target.pk), target_repr=target.email,
            ip_address=_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:512],
            detail={"by_admin": True, "mode": "email_link"},
        )
        messages.success(
            request,
            f"Reset link emailed to {target.email}. {n} active session(s) ended. "
            f"Token expires in 30 minutes."
        )
        return redirect("accounts:user-detail", pk=target.pk)

    # Otherwise issue a temp password (inline or email)
    pw = _generate_temp_password()
    target.set_password(pw)
    target.force_password_change = True
    target.save(update_fields=["password", "force_password_change"])
    n = _kill_user_sessions_helper(target)

    detail = {"by_admin": True, "mode": mode}
    AuditLog.objects.create(
        actor=request.user, action=AuditAction.PASSWORD_RESET,
        target_type="User", target_id=str(target.pk), target_repr=target.email,
        ip_address=_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:512],
        detail=detail,
    )

    if mode == "email_password":
        if not target.email:
            messages.warning(
                request,
                f"{target.email} has no email — falling back to inline display. "
                f"Temporary password: {pw}. Share it securely."
            )
        else:
            from django.urls import reverse as _reverse
            from notifications.models import Channel, Template
            from notifications.services import enqueue_notification
            login_url = request.build_absolute_uri(_reverse("accounts:login"))
            enqueue_notification(
                template=Template.ADMIN_PASSWORD,
                channel=Channel.EMAIL,
                recipient=target.email,
                context={
                    "user_name": target.get_full_name() or target.email,
                    "email": target.email,
                    "temp_password": pw,
                    "login_url": login_url,
                },
                user=target,
            )
            messages.success(
                request,
                f"Temporary password emailed to {target.email}. "
                f"{n} active session(s) ended. They'll be required to change it on first login."
            )
        return redirect("accounts:user-detail", pk=target.pk)

    # inline — show password in flash
    messages.success(
        request,
        f"Password reset for {target.email}. Temporary password: {pw} — "
        f"share securely. {n} active session(s) ended."
    )
    return redirect("accounts:user-detail", pk=target.pk)
