"""Abstraction over Minecraft identity/skin lookup so the profile UI never
talks to a specific third-party provider directly. Swap providers here later
without touching any template or route.

Strategy:
1. Resolve the username to a Mojang UUID (authoritative, catches typos/renames).
2. Build render URLs from the UUID via Crafatar (documented, open-source renderer).
3. If the lookup fails for ANY reason (network error, rate limit, unknown name,
   Mojang API down), fall back to a username-keyed renderer (mc-heads.net) which
   resolves server-side on its own and degrades to a default Steve/Alex skin
   rather than a broken image. The profile page never breaks either way.

Results are cached in-process with a TTL so a hovered/rendered profile doesn't
hit an external API on every page view, while still picking up skin changes
within a reasonable window.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

CACHE_TTL_OK = 60 * 60        # 1 hour for a successful resolution
CACHE_TTL_FAIL = 5 * 60       # 5 minutes for a failed lookup (don't hammer the API on a typo)
REQUEST_TIMEOUT = 3           # seconds; a slow external API must never stall the page

_cache = {}


class MinecraftProfileService:
    @staticmethod
    def get_profile(username):
        """Returns {username, uuid, avatar_url, render_url, resolved} or None
        if no username was given. `resolved` is False when we had to fall back
        to the username-keyed renderer without confirming the account exists."""
        if not username or not username.strip():
            return None
        key = username.strip().lower()
        now = time.time()
        cached = _cache.get(key)
        if cached and cached["expires"] > now:
            return cached["data"]

        data = MinecraftProfileService._resolve(username.strip())
        ttl = CACHE_TTL_OK if data["resolved"] else CACHE_TTL_FAIL
        _cache[key] = {"data": data, "expires": now + ttl}
        return data

    @staticmethod
    def _resolve(username):
        uuid = MinecraftProfileService._lookup_uuid(username)
        if uuid:
            return {
                "username": username,
                "uuid": uuid,
                "avatar_url": f"https://crafatar.com/avatars/{uuid}?size=128&overlay",
                "render_url": f"https://crafatar.com/renders/body/{uuid}?scale=8&overlay",
                "resolved": True,
            }
        # Graceful fallback: resolves by username on the renderer's own side,
        # and shows a default skin rather than a broken image if the name is
        # unknown. The page keeps working either way.
        safe_name = urllib.parse.quote(username)
        return {
            "username": username,
            "uuid": None,
            "avatar_url": f"https://mc-heads.net/avatar/{safe_name}/128",
            "render_url": f"https://mc-heads.net/body/{safe_name}/300",
            "resolved": False,
        }

    @staticmethod
    def _lookup_uuid(username):
        try:
            url = "https://api.mojang.com/users/profiles/minecraft/" + urllib.parse.quote(username)
            req = urllib.request.Request(url, headers={"User-Agent": "EurovillageHerald/1.0"})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                if resp.status != 200:
                    return None
                payload = json.loads(resp.read().decode("utf-8"))
            return payload.get("id") or None
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
            return None
        except Exception:
            # Never let an unexpected error from a third-party API break the page.
            return None

    @staticmethod
    def clear_cache():
        _cache.clear()
