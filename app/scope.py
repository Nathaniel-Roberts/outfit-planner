"""Whose wardrobe are we looking at?

Data is per user. The admin can view another user's data by choosing them in
Settings, which sets a cookie. Everyone else only ever sees their own.
"""

from __future__ import annotations

import sqlite3

from fastapi import Request

from app import users
from app.users import User

VIEW_COOKIE = "op_view_user"


def viewing_user(request: Request, conn: sqlite3.Connection, user: User) -> User:
    if not user.is_admin:
        return user
    raw = request.cookies.get(VIEW_COOKIE)
    if raw and raw.isdigit():
        target = users.get_by_id(conn, int(raw))
        if target is not None:
            return target
    return user
