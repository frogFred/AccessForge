from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.contrib.auth import get_user_model


def create_superuser_from_env() -> None:
    username = os.getenv("DJANGO_SUPERUSER_USERNAME")
    email = os.getenv("DJANGO_SUPERUSER_EMAIL")
    password = os.getenv("DJANGO_SUPERUSER_PASSWORD")
    if not username or not email or not password:
        return

    user_model = get_user_model()
    if user_model.objects.filter(username=username).exists():
        return

    user_model.objects.create_superuser(
        username=username,
        email=email,
        password=password,
    )
    print(f"Created superuser: {username}")


if __name__ == "__main__":
    create_superuser_from_env()
