# Cloudflare dashboard setup checklist

Everything in this file has to be done by hand in the Cloudflare dashboard —
none of it can be set up from the codebase. Once done, the corresponding
environment variables (see `.env.example`) plug the app into it.

**Where things stand right now, before you do any of this:** the app is
live on Railway without Turnstile or R2 configured yet. It keeps running
fine in that state — the public site is reachable, uploads go to local
disk — but it now prints a loud warning to the Railway logs on every boot
for each of the two, so this "not configured yet" state is visible rather
than silent. See `.env.example`'s `APP_ENV` / `TURNSTILE_REQUIRED` /
`R2_REQUIRED` notes for how that strictness is controlled, and set the
`_REQUIRED` flags once you've finished the steps below and want a hard
guarantee against ever silently falling back again.

## 1. Turnstile (visitor gate + form protection)

1. Cloudflare dashboard → **Turnstile** → **Add widget**.
2. Widget mode: **Managed**.
3. Domains: `eurovillageherald.com`, `www.eurovillageherald.com` (add any
   other hostname the public site is reachable on).
4. Create it, then copy the **Site Key** and **Secret Key** into
   `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY` on Railway.
5. That's it — the same widget protects both the site-wide visitor gate and
   the contact form (and any future form built with `turnstile.check()`).
   You do not need a second widget unless you specifically want separate
   analytics per surface.
6. Once both keys are set on Railway and you've confirmed the gate works,
   consider setting `TURNSTILE_REQUIRED=true` as well — this makes the app
   refuse to start in production if these keys are ever unset again (a
   deploy with a wiped env var, a misconfigured environment, etc.), instead
   of quietly booting unprotected. Optional, but recommended once this is
   genuinely in place.

## 2. R2 (media storage)

1. Cloudflare dashboard → **R2** → **Create bucket**. Name it something like
   `eurovillage-herald-media`.
2. **R2** → **Manage API Tokens** → **Create API Token**:
   - Permissions: **Object Read & Write**.
   - Scope it to the bucket you just created (not account-wide).
   - Save the **Access Key ID** and **Secret Access Key** immediately — R2
     shows the secret only once.
3. Set on Railway: `R2_ACCOUNT_ID` (dashboard sidebar), `R2_ACCESS_KEY_ID`,
   `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`.
4. **Connect the custom domain**: open the bucket → **Settings** →
   **Custom Domains** → **Connect Domain** → enter
   `matbaa.eurovillageherald.com` → follow the prompts (Cloudflare manages
   the DNS record for you since the zone is already on Cloudflare).
   **Do not** use `cdn.eurovillageherald.com` — the app is built around
   `matbaa.eurovillageherald.com` specifically.
5. Set `R2_PUBLIC_BASE_URL=https://matbaa.eurovillageherald.com` on Railway.
6. Recommended cache settings for the custom domain (R2 → bucket →
   Settings, or via a Cache Rule on the zone): the app already sets
   `Cache-Control: public, max-age=31536000, immutable` on every object it
   uploads, so Cloudflare's default edge caching for R2 custom domains is
   sufficient — no extra Page Rule is required. If you later add a Cache
   Rule anyway, make sure it respects origin cache-control rather than
   overriding it with a short TTL.
7. **Do not** point `matbaa.eurovillageherald.com` at the existing Railway
   wildcard — the custom-domain connection in step 4 creates its own DNS
   record that Cloudflare resolves directly to R2, which correctly takes
   precedence over the `*.eurovillageherald.com` wildcard used for Railway.
8. Set all five `R2_*` variables together in the same Railway deploy, not
   one at a time across multiple deploys — the app treats "some but not all
   five set" as a configuration error in production and will reject media
   uploads outright (with a clear error to the admin) rather than silently
   using local disk, specifically so a half-finished rollout of these
   variables can't go unnoticed. Once all five are confirmed working,
   consider also setting `R2_REQUIRED=true` for a permanent guarantee that
   uploads never silently fall back to local disk again, even during a
   future R2 outage or an accidentally-unset variable.

## 2b. A private bucket for reader-tip images (recommended)

Reader-submitted contact-form tip images are stored under an `okur-ihbarlari/`
prefix and the app never generates or shows a public URL for them — admins
view them only through an authenticated proxy route. **But** that alone is
not real confidentiality: R2 custom domains serve any object in the bucket
by key with no access check by default, so if these images share the public
bucket from step 2, anyone who obtained the exact object key could still
fetch it directly through `matbaa.eurovillageherald.com`, bypassing the
admin-only proxy. To close that gap:

1. Cloudflare dashboard → **R2** → **Create bucket** again — a second one,
   e.g. `eurovillage-herald-tips`.
2. **Do not** connect any custom domain to this bucket. That omission is
   the entire point: with no custom domain, the only way to reach an
   object in it is the S3 API with your account's R2 credentials, which
   only this application's backend has.
3. Your existing API token (from step 2) may already have access if it
   wasn't scoped to a single bucket; if it was, create a second token (or
   widen the existing one) with Object Read & Write on this new bucket too.
4. Set `R2_PRIVATE_BUCKET_NAME` on Railway to this bucket's name. No other
   new variables are needed — it reuses the same account/credentials/
   endpoint as the public bucket.
5. Until this is set, the app prints a startup warning explaining that tip
   images are only as private as "nobody happens to guess the URL", which
   is an honest description of the risk, not a real guarantee — set this
   variable when you're ready to close it.

## 3. Access (admin panel)

1. Zero Trust dashboard → **Access** → **Applications** → **Add an
   application** → **Self-hosted**.
2. Application domain: `admin.eurovillageherald.com` (leave the path as
   the whole domain — the entire admin app should sit behind Access).
3. **Identity providers**: enable **One-time PIN** (email OTP) — no extra
   IdP setup needed for this method.
4. **Policies**: add an **Allow** policy scoped to your team, e.g. "Include
   → Emails → {your team's email addresses}" (or an email domain rule if
   everyone shares one). Do not leave a default "Allow everyone" policy.
5. Save. Note the **Application Audience (AUD) Tag** shown on the
   application's overview page.
6. Your **team domain** is `<your-team-name>.cloudflareaccess.com`, shown
   under **Settings** → **Custom Pages** (or in the URL when you access the
   Zero Trust dashboard).
7. (Optional, defense-in-depth) Set `CF_ACCESS_TEAM_DOMAIN` and
   `CF_ACCESS_AUD` on Railway from steps 5/6 to make the app itself verify
   the signed Access identity on every admin request, in addition to Access
   blocking unauthorized requests at the edge. Leave both blank if you'd
   rather rely on Access alone — the admin panel is still fully protected
   either way.
8. Confirm `admin.eurovillageherald.com`'s DNS record is **proxied**
   (orange cloud) — Access only intercepts proxied traffic.

## 4. WAF / rate limiting (recommended, optional)

These are extra hardening on top of the app's own basic in-process rate
limits (login, contact form, gate check) — recommended but not required
for the app to function:

- **Security** → **WAF** → enable the **Cloudflare Managed Ruleset** on the
  zone if not already on.
- A **Rate Limiting Rule** on `POST /admin/login` (e.g. 15 requests per 5
  minutes per IP) and on `POST /iletisim` (e.g. 10 per 10 minutes per IP)
  adds edge-level protection in front of the app's own limiter, which is
  useful because the app's limiter is per-process and won't perfectly
  cover every gunicorn worker on its own.
- Consider a **Bot Fight Mode** / **Super Bot Fight Mode** toggle under
  **Security** → **Bots** for additional baseline protection on the public
  site.

## 5. Railway

Nothing new to configure in Railway's own dashboard beyond what already
exists (the `*.eurovillageherald.com` wildcard custom domain, which stays
exactly as-is) — just add the environment variables from `.env.example` to
the Railway service once you've completed steps 1–3 above. You do not need
to set `APP_ENV` explicitly — since `SERVER_NAME` is already set on
Railway, the app already infers production mode from that; `APP_ENV` is
there only if you ever need to override the inference (e.g. for a staging
service you want treated as development). Keep
`admin.eurovillageherald.com` and the wildcard both pointed at the same
Railway service as before; Cloudflare's more specific R2 custom domain for
`matbaa.eurovillageherald.com` and Access application for
`admin.eurovillageherald.com` take precedence over the wildcard
automatically because Cloudflare matches the most specific hostname
record.
