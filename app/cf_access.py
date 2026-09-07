"""Optional, defense-in-depth verification that an admin request actually
passed through Cloudflare Access.

Cloudflare Access is configured entirely from the Cloudflare dashboard and
is what actually protects admin.eurovillageherald.com -- unauthorized
requests never reach this app at all. This module does NOT replace that;
it only lets the app double-check the signed identity Access attaches to
requests that do get through, for audit-logging and an extra guard rail.

Entirely inert unless BOTH env vars below are set, so the app is not
coupled to Cloudflare Access by default -- it only activates once you
deliberately turn it on after configuring Access.
"""
import os

TEAM_DOMAIN = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip()
AUD = os.environ.get("CF_ACCESS_AUD", "").strip()
ENABLED = bool(TEAM_DOMAIN and AUD)

_jwk_client = None


def _client():
    global _jwk_client
    if _jwk_client is None:
        from jwt import PyJWKClient
        certs_url = f"https://{TEAM_DOMAIN}.cloudflareaccess.com/cdn-cgi/access/certs"
        _jwk_client = PyJWKClient(certs_url)
    return _jwk_client


def verified_identity(request):
    """Returns the verified Cloudflare Access email for this request, or
    None if Access isn't configured, the assertion is missing, or it fails
    signature/audience verification. Never raises."""
    if not ENABLED:
        return None
    token = request.headers.get("Cf-Access-Jwt-Assertion") or request.cookies.get("CF_Authorization")
    if not token:
        return None
    try:
        import jwt
        signing_key = _client().get_signing_key_from_jwt(token)
        claims = jwt.decode(token, signing_key.key, algorithms=["RS256"], audience=AUD)
        return claims.get("email")
    except Exception:
        return None
