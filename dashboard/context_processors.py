"""Context processors for dashboard — notifications badge data available to every template."""


def notifications(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"unread_notifications_count": 0, "notifications": []}
    # Notifications are not yet implemented — return safe defaults so the
    # header bell renders without unread badge.
    return {"unread_notifications_count": 0, "notifications": []}
