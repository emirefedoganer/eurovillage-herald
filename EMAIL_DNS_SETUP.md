# Email DNS setup: SPF, DKIM, DMARC (Resend)

**Nothing in this file has been applied or checked against live systems.** This repository has
no access to Resend, Cloudflare or Zoho, so no DNS was changed and no record below has been
compared with the current Resend Domains screen.

## What the app sends from (from configuration defaults)
| Purpose | Address | Domain |
|---|---|---|
| Transactional (`EMAIL_TRANSACTIONAL_FROM`) | `noreply@eurovillageherald.com` | `eurovillageherald.com` |
| Bulletins (`EMAIL_BULLETIN_FROM`) | `bulletin@news.eurovillageherald.com` | `news.eurovillageherald.com` |
| Reply-To only (`EMAIL_CONTACT_REPLY_TO`) | `iletisim@eurovillageherald.com` | Zoho mailbox - never a sender; do not modify its MX or mailbox settings |

## Confirmed vs. owner-must-verify
| Item | Status |
|---|---|
| Sender addresses/domains above | Confirmed from `app/mailer.py` defaults (production env vars may override - check Railway) |
| Resend-required SPF/DKIM/Return-Path records | **Owner must read them from Resend -> Domains.** Not in this repo; never copy them from elsewhere |
| Whether `eurovillageherald.com` AND `news.eurovillageherald.com` are each added to Resend as separate sending domains | **Unknown - owner must check.** If only one is added, mail from the other domain will fail Resend's domain check |
| Existing `_dmarc` record at the root | **Unknown - owner must look it up** (`dig +short TXT _dmarc.eurovillageherald.com`) |
| A mailbox that can receive DMARC aggregate reports | Only `iletisim@eurovillageherald.com` is known to exist; whether it should receive XML reports is the owner's call |

## DMARC record (proposed, owner-verified before use)
| Field | Value |
|---|---|
| Record type | `TXT` (DNS-only; TXT records cannot be proxied) |
| Hostname | `_dmarc` in the `eurovillageherald.com` zone, i.e. `_dmarc.eurovillageherald.com` |
| Complete TXT value | `v=DMARC1; p=none;` |
| TTL | Auto (Cloudflare default) or 3600 |
| Scope | The organizational-domain record also governs `news.eurovillageherald.com` (subdomains inherit unless they publish their own `_dmarc` record or the root sets `sp=`). A separate `_dmarc.news` record is **not required** unless you want a different policy there |

This value is a valid, monitoring-only DMARC record. It is **not** claimed to be what Resend's
screen currently displays; if Resend shows a different DMARC suggestion, compare and follow
the differences deliberately.

- `rua=` is intentionally **omitted**: no report-receiving mailbox/alias has been confirmed.
  Add `rua=mailto:<confirmed-address>;` later only once one exists.
- If a `_dmarc` TXT already exists, **edit it** - two DMARC records make both invalid.
- Keep exactly one `v=spf1` TXT per hostname; if a Zoho SPF exists on the root, merge Resend's
  include into that record instead of creating a second one. Do not touch Zoho MX records.

## Cloudflare steps
1. Resend -> Domains: note which domains are added and copy the exact records shown for each.
2. Cloudflare -> DNS -> Records: look for an existing `_dmarc` TXT. Edit it if present; else Add record.
3. Type `TXT`, Name `_dmarc`, Content `v=DMARC1; p=none;`, TTL Auto, Save.

## Verification (after the owner applies it)
```bash
dig +short TXT _dmarc.eurovillageherald.com
dig +short TXT news.eurovillageherald.com    # check SPF is a single record
```
Then Resend -> Domains -> verify each added domain. Only with an explicitly authorized test
message, open it in Resend -> Emails and confirm From, Reply-To
`iletisim@eurovillageherald.com`, and `dkim=pass`, `spf=pass`, `dmarc=pass` in the headers.

## Later hardening
Stay on `p=none` while reviewing reports. Consider `p=quarantine` then `p=reject` only after
SPF/DKIM alignment has passed consistently. Never automatic.

## Webhooks
Register `https://<domain>/internal/webhooks/resend` in Resend (delivered, bounced, complained,
failed) and set `RESEND_WEBHOOK_SECRET` in Railway.
