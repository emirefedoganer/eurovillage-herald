# Changelog

## Unreleased

- **Reply-To**: verified end to end (call site -> outbox -> mailer -> Resend SDK `reply_to`);
  subscription verification/confirmation mail now carries it too. Fake backend exposes
  `mailer.FAKE_SENT`. Outbox retries use backoff with a visible next-retry time.
- **Newsletter**: new `bounced`/`complained`/`suppressed` states, Svix-verified Resend webhook
  (`/internal/webhooks/resend`, `RESEND_WEBHOOK_SECRET`), send-time eligibility recheck,
  admin recovery via fresh double opt-in.
- **PDF reader** reverted to its pre-1.0.1 interface (commit `7f00944`); security, preview,
  analytics kept.
- **Public navbar** restored (no subscribe link); subscription now in homepage right column.
- Removed the "Bu Haber Gazetede" article box (data relationship kept).
- Privacy notice rewritten without placeholders; `EMAIL_DNS_SETUP.md` added.

## The Eurovillage Herald 1.3.0

**Site-wide responsive/overflow pass.** Audited every major public and
admin page at 320/375/390/768/1024/1280/1440px. Fixed a set of shared,
structural causes rather than patching individual pages: missing
`minmax(0, ...)` on several CSS Grid tracks (the classic Grid overflow
trap, where a bare `1fr`/fixed-px track can still grow past its content's
intrinsic size), admin data tables with no responsive treatment at all
(every `<table class="admin-table">` is now wrapped in a horizontally
scrollable `.table-scroll` container, with cells defaulting to a single
line so a too-wide table scrolls cleanly instead of every cell wrapping
into an unreadable column), and a subscribe-widget flex-basis bug that
left a large blank gap on mobile (caught only through visual screenshot
review, not the automated overflow check — see below).

**Redesigned admin navigation (TEH Admin).** The 13+ item single-row
nav that prompted this pass is now three logical dropdown groups (Gazete
/ İletişim & Abonelik / Yönetim) built on native `<details>/<summary>` —
zero new JS framework, keyboard/screen-reader accessible by default,
collapsing to an accordion on narrow screens. Authorization is
unchanged: this is presentation only, still enforced server-side by the
same `master_admin_required`/permission checks as before.

**Playwright responsive smoke tests** (`tests/responsive/`, new
`requirements-dev.txt`) against a real running instance of the app:
page-level overflow checks across every major page × 7 widths, PLUS a
component-scoped check for the main nav specifically — the page-level
check structurally cannot catch a component with its own
`overflow-x:auto` silently clipping content, which is exactly the real
bug this pass found (an 11th nav item pushed the search box off the
edge at ordinary desktop widths like 1024–1280px).

**Redesigned advertising placement system (`ads.py`).** A slot registry
where every entry now documents its human-readable Turkish label, where
it appears, compatible page types, creative aspect ratio, and explicit
desktop/tablet/mobile behavior — surfaced live in the admin placement
form via a contextual preview panel/diagram, so choosing "Makale — Kenar
Çubuğu" shows exactly where that puts the ad, with no route/template
name ever exposed to an admin. Adds a genuine article sidebar (new
`.article-layout` grid in `article.html`, only reserving column space
when a sidebar ad actually exists) with an admin-configurable mobile
fallback (after the article content, or hidden). The selection algorithm
now honors priority and weight (a documented, deterministic day-seeded
rotation — never Python's `random`) within a specificity tier
(article > section > site-wide, unchanged), adds device targeting
(desktop/tablet/mobile, coexisting with an "all devices" placement for
the same slot without conflict), same-page deduplication, and a
configurable per-page cap. Legacy dotted slot identifiers
(`homepage.top`) are transparently translated to their new names
(`homepage_top`) on every read — no data migration required, and a
genuinely unrecognized slot is flagged in the admin dashboard rather
than silently dropped.

**Newsletter subscription is now visible on the public site.** The
subscription backend (double opt-in, Turnstile, rate limiting,
preferences) already existed but had no public entry point. Reuses it
unchanged via one shared macro (`_macros.html`: `subscribe_form()`) in
five places: the main nav and footer (a "Bültene Abone Ol" link), a
dedicated homepage section, a compact end-of-article prompt, and a new
standalone `/bultene-abone-ol` landing page — plus an optional,
admin-controlled (default off) subscribe form on the Standby page (see
Site Control), which required specifically exempting `subscribe_submit`
from the standby gate so the form it renders actually works.

## The Eurovillage Herald 1.2.0

**Site Control (TEH Admin → Site Kontrolü).** A single, database-backed
(`site_control.json`) switch between three public-site modes: Live
(normal), Standby (a polished, branded maintenance page in the site's own
visual identity, returned with HTTP 503 and `noindex, nofollow`), and
Redirect (a temporary 302 to an admin-entered URL, never hardcoded).
Implemented as one centralized `before_request` hook rather than a
per-view check, so no route needed to change. Defaults safely to Live
when no settings record exists yet, and a corrupt/unreadable settings
file also fails open to Live rather than locking the public site (or an
admin) out.

The admin panel, login, static assets, `/healthz`, and every other
technical endpoint are always excluded from Standby/Redirect -- enforced
server-side, not by hiding navigation. Any authenticated admin session
additionally sees the real public site during Standby/Redirect, via the
existing login cookie (never a guessable query parameter). Master-Admin
-only, consistent with the newspaper/bulletin/subscriber administration
hardening in the previous release.

Redirect destinations are validated server-side (http/https only, no
`javascript:`/other dangerous schemes, never the Herald's own host),
always a temporary redirect (302, never 301), and include a short-lived
bounce-detection cookie that breaks a mutual loop if the destination
itself redirects back to the Herald. JSON/API endpoints (analytics
beacons, crossword/sudoku check endpoints) always get a JSON 503 during
Standby/Redirect, never the HTML standby page. Recent redirect
destinations are offered back as quick-fill presets, never activated
automatically.

## The Eurovillage Herald 1.1.0

The production communication system: a provider-agnostic email layer, a
durable outbox, editorial bulletins, and a contact-form-to-ticket
workflow, all controllable from TEH Admin rather than an external
provider dashboard. Every existing subscription/analytics/contact/issue
behavior from 1.0.1 is preserved and extended, not replaced.

**Email provider abstraction (`mailer.py`).** `EMAIL_PROVIDER` selects a
backend (`fake` by default -- logs only, sends nothing; `smtp`; `resend`),
behind exactly two entry points: `send_transactional_email()` (from
`noreply@eurovillageherald.com`) and `send_bulletin_email()` (from
`bulletin@news.eurovillageherald.com`). Neither address is ever a
monitored inbox. Contact-flavored transactional mail carries
`Reply-To: iletisim@eurovillageherald.com` -- the existing, real
Zoho-hosted mailbox, which this release never creates, modifies, or
synchronizes with in any way.

**Durable email outbox (`outbox.py`).** Every outgoing email is queued
(QUEUED/PROCESSING/SENT/FAILED) before it is ever sent, drained
opportunistically on ordinary page loads (the same pattern already used
for scheduled issue publishing) plus a manual admin action and an
optional bearer-token Railway Cron endpoint. Idempotency keys prevent
duplicate confirmation/acknowledgement/campaign emails across retries or
redeploys; a job stuck mid-send after a crash is recovered automatically.

**Multi-category bulletin subscriptions.** The "yeni sayı" subscription
system now supports five independent preference categories (new issue,
weekly digest, popular stories, breaking news, Arı Magazin), a no-login
preference-management page, per-category unsubscribe links, and KVKK
privacy-notice-version/consent tracking recorded on every signup and
preference change.

**Editorial bulletin CMS (`bulletins.py`, TEH Admin → Bültenler).**
Draft-first campaigns (draft → scheduled → sent/cancelled) with manual
article selection, ordering, and lead-story control; "most read" analytics
are surfaced as suggestions only and never auto-selected. Test sends reach
only explicitly entered addresses and are clearly marked, never touching
the real subscriber list. A real send requires an explicit audience-safety
confirmation screen before anything is queued. Publishing a newspaper
issue automatically creates and sends its NEW_ISSUE bulletin exactly once,
defaulting to every article linked to that issue; issue publication itself
never fails due to an email-provider outage.

**Contact form → ticket workflow.** Every contact submission now gets a
human-readable reference (`TEH-2026-0042`) and a status lifecycle
(Yeni → İnceleniyor → Ek Bilgi Bekleniyor → Çözüldü → Kapatıldı), visible
in TEH Admin's İletişim/Talepler view with open/resolved/all filtering. An
automatic acknowledgement email is queued when an address is given;
resolving a ticket requires an explicit "Çözüm mesajı gönder" action and
is never implied by an internal status change. A submission is never lost
to an email-provider outage -- the ticket persists regardless.

**Email System Health (TEH Admin → E-posta Sistemi).** Non-secret
visibility into the active backend, configured sender identities, and
outbox queue/failure counts, with safe per-job retry -- never a
"resend everything" control, and API keys/SMTP passwords are never
displayed here or anywhere else in the admin panel.

**KVKK/privacy notice (`/gizlilik`).** A site-styled privacy notice
covering what is collected and why, clearly marked with placeholders
where a legal/compliance decision (data controller identity, retention
periods, legal basis, provider/international-transfer disclosures) is
still required -- this release implements the technical framework only
and does not constitute legal compliance by itself.

## The Eurovillage Herald 1.0.1

A newspaper-platform release: The Eurovillage Herald's PDF newspaper issues
gain the same structured-publishing capabilities articles already had
(editorial workflow, scheduling, secure previews), plus new ways to connect
web articles to the printed paper, a modernized reader, first-party
analytics, reader notifications, and editorial automation support.

**Newspaper issue metadata & editorial workflow.** Issues are now proper
publishing entities rather than bare PDF records: an issue type (regular /
special / election special), status (Taslak → İncelemede → Hazır →
Zamanlanmış → Yayında → Arşivlendi), an internal editor's note, page count,
and file size, alongside everything an issue already had. Every existing
issue kept its PDF, cover, and URL exactly as they were.

**Scheduled issue publishing**, timestamp-driven and restart-safe — no
background scheduler. A scheduled issue goes live the moment its publish
time passes, checked on every relevant page load, the same mechanism
already used for scheduled articles.

**Secure editorial previews** for unpublished issues, via a high-entropy,
never-guessable link (never a stored plaintext, never indexable) — share a
draft or scheduled issue with someone outside the newsroom without
publishing it.

**"Bu Sayıdan" / "Bu Haber Gazetede."** An article can now optionally link
to a specific newspaper issue and page. The article page shows a "read it
in the paper" box linking straight to that page in the reader; the issue's
reader page lists every web article drawn from that issue.

**Shareable, page-addressable reader URLs** (`/gazete/<sayı>#page=4`) —
opening a link jumps straight to that page, turning pages updates the URL
without a reload, and a "share this page" button uses the Web Share API
where available (copy-link fallback otherwise).

**PDF Reader 2.0** — page thumbnails, jump-to-page, fit width/fit page,
fullscreen, keyboard and swipe navigation, and the reader remembers where
you left off per issue (localStorage). Still the same PDF.js-based, R2-backed
reader — the canonical source is always the stored `matbaa.eurovillageherald.com`
URL, never local static files.

**First-party, privacy-conscious newspaper analytics** — reader opens,
reading sessions, average pages viewed, completion rate, downloads, shares,
and "Gazetede Oku" clicks, per issue and newsroom-wide. No IP addresses are
ever stored; readers are distinguished only by an anonymous, browser-generated
token the reader can reset any time. See `app/analytics.py` for exactly
what is and isn't collected.

**Reader subscriptions** — "let me know when a new issue is published,"
double opt-in, with a secure, stateless unsubscribe link in every email.
Preferences are structured to support more categories (breaking news,
weekly digest, Arı Magazin) later without a schema change. Actual email
delivery requires SMTP configuration (see `.env.example`) — without it,
the feature still works end to end except the message isn't actually sent.

**Editorial draft automation** — publishing an issue can prepare a
DRAFT announcement bundle (announcement copy, newsletter intro, social
copy, SEO description, highlights, push copy) for human review in
`/admin/taslaklar`. Nothing generated here is ever published or sent
automatically; no AI provider is fabricated — draft text is deterministic
and template-based unless a real provider is later connected.

**Open Graph / social preview metadata** for articles and issues, using
the issue cover as the shared-link preview image. Preview/unpublished
issues are never made indexable or given a public social preview.

**New "newspaper" permission** — issue management can now be delegated to
a custom role, the same way Games/Messages/Site Settings already can be.
Master Admin is unaffected either way. The subscriber list stays
Master-Admin-only regardless, given its sensitivity.

---

No production media, credentials, or R2 configuration were touched by this
release. See `README.md` and `.env.example` for the new environment
variables (all optional, all safe to leave unset).
