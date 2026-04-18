"""Context processors for accounts — expose user's active role names to all templates."""


def user_roles(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"user_role_names": []}
    names = list(
        user.user_roles.filter(is_active=True).values_list("role__name", flat=True)
    )
    return {"user_role_names": names}
