from functools import wraps
from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def role_required(*roles):
    """
    Decorator for function-based views that checks whether the user has one of the required roles.
    - If unauthenticated: redirects to login.
    - If authenticated but missing the role: raises PermissionDenied (HTTP 403).
    - Superusers always pass.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f"/accounts/login/?next={request.path}")
            if request.user.is_superuser or request.user.role in roles:
                return view_func(request, *args, **kwargs)
            raise PermissionDenied("You do not have permission to access this resource.")
        return _wrapped_view
    return decorator


class RoleRequiredMixin(AccessMixin):
    """
    CBV mixin that verifies the logged-in user belongs to one of the specified allowed_roles.
    - If unauthenticated: handles no permission (redirects to login).
    - If authenticated but unauthorized: raises PermissionDenied (HTTP 403).
    - Superusers always pass.
    """
    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.is_superuser or request.user.role in self.allowed_roles:
            return super().dispatch(request, *args, **kwargs)
        raise PermissionDenied("You do not have permission to access this resource.")
