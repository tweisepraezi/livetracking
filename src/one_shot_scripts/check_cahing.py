import json
import logging
import os
from typing import List, TYPE_CHECKING

from django.core.cache import cache

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "live_tracking_map.settings")
    import django

    django.setup()

    print(cache.get("test_r"))
    cache.set("test_r", "5")
    print(cache.get("test_r"))
    # cache.delete_pattern("test*")
    # print(cache.get("test_r"))
