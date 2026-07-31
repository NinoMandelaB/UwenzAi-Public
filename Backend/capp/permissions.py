from rest_framework import permissions


class IsAdminOrSuperuser(permissions.BasePermission):
    """
    Grants access only to staff or superuser accounts.

    Used for the admin-panel API surface (capp/views/admin_views.py). Mirrors
    the is_staff/is_superuser check already used by MessageFlagService and
    SupportService, so admin permission semantics stay consistent across the
    codebase.
    """

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))
