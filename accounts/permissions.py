from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.contrib.auth.mixins import LoginRequiredMixin


def has_role(user, role_name):
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.user_roles.filter(
        is_active=True, role__is_active=True, role__name=role_name
    ).exists()


def has_any_role(user, *role_names):
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    if not role_names:
        return False
    return user.user_roles.filter(
        is_active=True, role__is_active=True, role__name__in=role_names
    ).exists()


def role_required(*role_names):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if not has_any_role(request.user, *role_names):
                raise PermissionDenied("Required role not assigned")
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


class RoleRequiredMixin(LoginRequiredMixin):
    required_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if not has_any_role(request.user, *self.required_roles):
            raise PermissionDenied("Required role not assigned")
        return super().dispatch(request, *args, **kwargs)
