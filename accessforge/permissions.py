from __future__ import annotations

from django.core.exceptions import PermissionDenied

from .models import ROLE_EDITOR, ROLE_OWNER, ROLE_VIEWER, DataTable


def require_table_role(table: DataTable, user, minimum_role: str = ROLE_VIEWER) -> None:
    if not table.has_role(user, minimum_role):
        raise PermissionDenied("You do not have permission to access this table.")


def require_table_editor(table: DataTable, user) -> None:
    require_table_role(table, user, ROLE_EDITOR)


def require_table_owner(table: DataTable, user) -> None:
    require_table_role(table, user, ROLE_OWNER)
