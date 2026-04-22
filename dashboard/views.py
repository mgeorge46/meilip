"""Dashboard views — home KPIs, global search, roadmap placeholder, errors."""
import json

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from . import services


@login_required
@require_GET
def home(request):
    """Employee dashboard landing page — live KPIs + charts."""
    cards = services.stat_cards()
    ageing = services.ar_ageing()
    trend = services.revenue_trend()
    notif = services.notification_health()
    context = {
        "page_title": "Dashboard",
        "cards": cards,
        "ageing": ageing,
        "ageing_json": json.dumps(ageing),
        "trend": trend,
        "trend_json": json.dumps(trend),
        "notif": notif,
        "top_arrears": services.top_arrears(),
        "recent_payments": services.recent_payments(),
    }
    return render(request, "dashboard/home.html", context)


@login_required
@require_GET
def kpi_api(request):
    """JSON endpoint for the dashboard — used when the user clicks
    the refresh button so charts update without a full reload."""
    return JsonResponse({
        "cards": services.stat_cards(),
        "ageing": services.ar_ageing(),
        "trend": services.revenue_trend(),
        "notif": services.notification_health(),
    })


@login_required
@require_GET
def global_search(request):
    """Four-table grouped search across tenants, houses, estates, users."""
    q = (request.GET.get("q") or "").strip()
    results = {"tenants": [], "houses": [], "estates": [], "users": []}
    if q:
        from core.models import Tenant, House, Estate
        from accounts.models import User

        results["tenants"] = Tenant.objects.filter(
            Q(full_name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(id_number__icontains=q),
        )[:25]

        results["houses"] = House.objects.filter(
            Q(name__icontains=q) | Q(house_number__icontains=q),
        ).select_related("estate")[:25]

        results["estates"] = Estate.objects.filter(
            Q(name__icontains=q) | Q(location__icontains=q),
        )[:25]

        results["users"] = User.objects.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q),
        )[:25]

    return render(
        request,
        "dashboard/search.html",
        {"page_title": f'Search: "{q}"' if q else "Search", "q": q, "results": results},
    )


ROADMAP = {
    "invoices": ("Invoices", "Invoice create / void / credit-note UI on top of the existing billing services."),
    "payments": ("Payments", "Manual payment entry form with maker-checker flow for cash receipts."),
    "receipts": ("Receipts", "Receipt lookup + reprint with PDF export."),
    "invoice-schedules": ("Invoice Schedules", "Per-tenancy schedule overrides and pause/resume UI."),
    "trial-balance": ("Trial Balance", "Period-end trial balance export with drill-through to journal entries."),
    "landlord-statements": ("Landlord Statements", "Per-period statement with PDF export matching the sample report format."),
    "admin-settings": ("Admin Settings", "Tenant-wide settings: branding, tax defaults, beat schedule overrides."),
}


@login_required
def coming_soon(request, feature):
    label, description = ROADMAP.get(
        feature, (feature.replace("-", " ").title(), ""),
    )
    other_roadmap = [(slug, item) for slug, item in ROADMAP.items() if slug != feature]
    return render(
        request,
        "dashboard/coming_soon.html",
        {
            "page_title": label,
            "feature": label,
            "description": description,
            "other_roadmap": other_roadmap,
            "feature_slug": feature,
        },
    )


# --- Custom error handlers --------------------------------------------------
def handler403(request, exception=None):
    return render(request, "errors/403.html", {"page_title": "Forbidden"}, status=403)


def handler404(request, exception=None):
    return render(request, "errors/404.html", {"page_title": "Not Found"}, status=404)


def handler500(request):
    return render(request, "errors/500.html", {"page_title": "Server Error"}, status=500)
