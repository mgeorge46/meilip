"""Dashboard views — home, global search, coming-soon placeholder, custom errors."""
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render
from django.views.decorators.http import require_GET


@login_required
@require_GET
def home(request):
    """Employee dashboard landing page."""
    return render(request, "dashboard/home.html", {"page_title": "Dashboard"})


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


@login_required
def coming_soon(request, feature):
    return render(
        request,
        "dashboard/coming_soon.html",
        {"page_title": "Coming Soon", "feature": feature.replace("-", " ").title()},
    )


# --- Custom error handlers --------------------------------------------------
def handler403(request, exception=None):
    return render(request, "errors/403.html", {"page_title": "Forbidden"}, status=403)


def handler404(request, exception=None):
    return render(request, "errors/404.html", {"page_title": "Not Found"}, status=404)


def handler500(request):
    return render(request, "errors/500.html", {"page_title": "Server Error"}, status=500)
