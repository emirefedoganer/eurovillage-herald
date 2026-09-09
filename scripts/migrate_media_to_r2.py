#!/usr/bin/env python3
"""Migrates ALL legacy PUBLIC Herald media to the public Cloudflare R2
bucket (R2_BUCKET_NAME, served from R2_PUBLIC_BASE_URL -- normally
https://matbaa.eurovillageherald.com), and rewrites the JSON records that
reference it. Covers every public media type the application has:

  - article images (haber)              app/data/articles.json  .image
  - Arı Magazin article images          app/data/articles.json  .image (section == "magazin")
  - author profile/cover images         app/data/authors.json   .profile_image / .cover_image
  - Gazete Yönetimi (masthead) images   app/data/newspaper_management.json  .image_override
  - advertisement creative images       app/data/ads.json       .image
  - newspaper issue PDFs                app/data/issues.json    .pdf
  - newspaper issue cover images        app/data/issues.json    .cover_image

Reader-tip images are explicitly OUT OF SCOPE -- they are private-by-design
(see app/uploads.py:save_tip_image / app/storage.py's private-bucket
helpers) and belong in R2_PRIVATE_BUCKET_NAME under okur-ihbarlari/, never
in the public bucket this script writes to. This script never reads
app/private_uploads/ and never uses the okur-ihbarlari/ prefix.

THIS SCRIPT NEVER RUNS AUTOMATICALLY. It is not imported by app.py, not
called from start.sh/Procfile, and has no side effects at import time. It
only ever does anything when a human runs it directly.

Safe by construction
---------------------
  - DRY RUN IS THE DEFAULT and makes ZERO changes: it never opens a JSON
    data file for writing, never uploads anything, and never touches
    store.py's self-healing load_*() functions (which themselves write a
    backfilled id/status/etc. back to disk as a side effect of merely
    reading -- this script reads every JSON file itself, directly, with a
    plain read-only json.load(), specifically to avoid that).
  - Idempotent: a reference that already points at our own R2 public URL
    is left alone ("already migrated"); a computed object key that
    already exists in R2 (e.g. a previous run uploaded it but the process
    was killed before the JSON record was updated) is NOT re-uploaded --
    the record is simply pointed at it. Re-running this script after a
    full, successful migration changes nothing at all.
  - Every JSON file this script is about to modify is backed up (a
    timestamped copy under app/data/.media_migration_backups/) before the
    first write of an --execute run, written atomically (temp file +
    os.replace), and the temp file is read back and re-parsed as JSON to
    confirm it is well-formed *before* it replaces the original -- a
    write that fails validation leaves the original file untouched.
  - Legacy local files are NEVER deleted, moved, or modified by this
    script, under any mode, ever.
  - Per object: locate the local source -> validate it's an allowed type
    -> compute a stable, deterministic R2 key -> check whether that key
    already exists in R2 -> upload only if it doesn't -> confirm the
    upload actually landed (a follow-up HEAD request) -> only THEN update
    the JSON record. A failure at any step is recorded and the script
    moves on to the next item; it never leaves a record pointing at a
    URL that doesn't actually resolve.

Usage
-----
    python3 scripts/migrate_media_to_r2.py                # dry run (default, zero changes)
    python3 scripts/migrate_media_to_r2.py --execute       # actually upload + rewrite records
    python3 scripts/migrate_media_to_r2.py --verify        # check R2 + records, zero changes

Requires the app's normal R2_* environment variables to already be set
(R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET_NAME /
R2_PUBLIC_BASE_URL -- see .env.example) in every mode, including dry run:
a dry run still needs to ask R2 "does this object already exist?" to
report accurate already-migrated/would-upload status, it just never
writes anything itself.

Running this in Railway production
-----------------------------------
Legacy files live on Railway's persistent volume, which start.sh mounts
by symlinking app/data, app/static/img/articles, app/static/img/authors
and app/static/issues onto it at container boot (see start.sh). That
symlinking only happens when start.sh actually runs as the container's
entrypoint. `railway run <cmd>` does NOT do this -- it runs <cmd> on your
own machine with the linked service's environment variables injected,
which would read your LOCAL filesystem while holding PRODUCTION R2
credentials. Do not use it for this script. Use `railway ssh` to get a
shell inside the already-running production container instead (see the
bottom of this file's --help output / CLOUDFLARE_SETUP.md for the exact
commands); inside that shell, start.sh has already run, so the app's own
paths already resolve onto the persistent volume correctly.

As a defensive fallback (in case this is ever invoked in an environment
where the volume is mounted but start.sh's symlinks haven't been created
yet), this script independently detects the persistent volume via the
same PERSIST_DIR environment variable start.sh uses (default /data) and
will read/write directly under it instead of the plain app/-relative path
when it finds the app/-relative path is NOT already pointing there. See
detect_paths() below, and this script's own printed "Path detection"
section, which is always shown first, in every mode.
"""
import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")
sys.path.insert(0, APP_DIR)

import storage  # noqa: E402
import uploads  # noqa: E402

PDF_CONTENT_TYPE = "application/pdf"

# The exact directories start.sh persists onto Railway's volume, and the
# subdirectory name it uses under PERSIST_ROOT for each one. This is not
# imported from start.sh (a shell script) -- it is transcribed here and
# must be kept in sync with it by hand if that script ever changes.
PERSIST_ROOT = os.environ.get("PERSIST_DIR", "/data").rstrip("/") or "/data"

PERSISTED_DIRS = {
    # label                 app-relative path                 persist-root subdir
    "data":            ("data",                                "data"),
    "img_articles":    (os.path.join("static", "img", "articles"), "img_articles"),
    "img_authors":     (os.path.join("static", "img", "authors"),  "img_authors"),
    "issues":          (os.path.join("static", "issues"),          "issues"),
}

# These two directories exist in the running app (app.py creates them on
# startup) but start.sh does NOT symlink them onto the persistent volume.
# Any file that ever landed here via the local-disk upload fallback lives
# only in that one container's ephemeral filesystem and will NOT survive
# the next deploy/restart. This script still scans them (ad/management
# images are legitimate public Herald media), it just reports this fact
# loudly rather than assuming they're safe.
NOT_PERSISTED_DIRS = {
    "img_management": os.path.join("static", "img", "management"),
    "img_ads": os.path.join("static", "img", "ads"),
}


# =========================================================================
# Path detection -- answers "where does the legacy filesystem actually put
# things", independent of whether start.sh's symlinks exist yet.
# =========================================================================

@dataclass
class PathInfo:
    label: str
    app_relative: str
    resolved_path: str
    persisted: bool
    detail: str


def _resolves_under(path, root):
    try:
        rp = os.path.realpath(path)
        rr = os.path.realpath(root)
    except OSError:
        return False
    return rp == rr or rp.startswith(rr + os.sep)


def detect_paths():
    """Returns (resolved, infos): `resolved` is a dict of label -> the
    directory this run will actually read/write (data, img_articles,
    img_authors, issues, img_management, img_ads); `infos` is a matching
    list of PathInfo, one per label, suitable for printing. Never creates
    or modifies anything on disk -- pure detection."""
    infos = []
    resolved = {}
    volume_present = os.path.isdir(PERSIST_ROOT)

    for label, (app_relative, persist_subdir) in PERSISTED_DIRS.items():
        app_path = os.path.join(APP_DIR, app_relative)
        volume_path = os.path.join(PERSIST_ROOT, persist_subdir)
        if _resolves_under(app_path, PERSIST_ROOT):
            chosen = app_path
            detail = f"symlinked to persistent volume ({os.path.realpath(app_path)})"
            persisted = True
        elif volume_present and os.path.isdir(volume_path):
            chosen = volume_path
            detail = (
                f"app path is NOT yet symlinked (start.sh has not run in this "
                f"process) -- using the volume path directly: {volume_path}"
            )
            persisted = True
        elif volume_present:
            chosen = app_path
            detail = (
                f"persistent volume is mounted at {PERSIST_ROOT} but "
                f"{volume_path} does not exist there yet -- falling back to "
                f"the app-relative path ({app_path}); if this is production, "
                f"investigate before proceeding"
            )
            persisted = False
        else:
            chosen = app_path
            detail = "no persistent volume detected -- local/app-relative path (expected in local dev)"
            persisted = False
        resolved[label] = chosen
        infos.append(PathInfo(label, app_relative, chosen, persisted, detail))

    for label, app_relative in NOT_PERSISTED_DIRS.items():
        app_path = os.path.join(APP_DIR, app_relative)
        resolved[label] = app_path
        infos.append(PathInfo(
            label, app_relative, app_path, False,
            "NOT part of start.sh's persisted volume map -- this directory lives only on "
            "the container's ephemeral filesystem in production. Any file here from the "
            "local-disk upload fallback will be LOST on the next deploy/restart unless it "
            "gets migrated to R2 (which is exactly what this script does).",
        ))

    return resolved, infos


def print_path_report(resolved, infos):
    bar = "=" * 78
    print(f"\n{bar}\nPATH DETECTION\n{bar}")
    print(f"PERSIST_DIR (Railway volume root): {PERSIST_ROOT}"
          f"{' (exists)' if os.path.isdir(PERSIST_ROOT) else ' (does not exist on this filesystem)'}")
    for info in infos:
        tag = "[persistent]" if info.persisted else "[EPHEMERAL] "
        print(f"\n  {tag} {info.label}")
        print(f"      app-relative path : app/{info.app_relative}")
        print(f"      resolved to       : {info.resolved_path}")
        print(f"      {info.detail}")
    print(f"\n  data directory (articles/authors/issues/ads/management JSON): {resolved['data']}")
    print(bar)


# =========================================================================
# Data-file paths, resolved via detect_paths() rather than store.py, and
# read with a plain json.load() -- NEVER store.load_articles() et al,
# which self-heal (and therefore WRITE) as a side effect of reading. This
# script must make zero changes in dry-run/verify, so it never calls them.
# =========================================================================

def data_paths(data_dir):
    return {
        "articles": os.path.join(data_dir, "articles.json"),
        "authors": os.path.join(data_dir, "authors.json"),
        "issues": os.path.join(data_dir, "issues.json"),
        "management": os.path.join(data_dir, "newspaper_management.json"),
        "ads": os.path.join(data_dir, "ads.json"),
    }


def read_json(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


BACKUP_DIR_NAME = ".media_migration_backups"


def write_json_safely(path, data, run_stamp):
    """Timestamped backup -> atomic write -> read-back validation -> swap.
    Raises on any failure, leaving the original file completely untouched
    (the original is never opened for writing until the very last step)."""
    backup_root = os.path.join(os.path.dirname(path), BACKUP_DIR_NAME, run_stamp)
    os.makedirs(backup_root, exist_ok=True)
    if os.path.exists(path):
        import shutil
        shutil.copy2(path, os.path.join(backup_root, os.path.basename(path)))

    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")

        with open(tmp_path, "r", encoding="utf-8") as f:
            reparsed = json.load(f)
        if reparsed != data:
            raise ValueError("JSON written to disk does not round-trip to the same data -- aborting this write")

        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


# =========================================================================
# Media item model + shared per-field pipeline
# =========================================================================

@dataclass
class MediaItem:
    media_type: str          # "article_image" | "magazine_image" | "author_profile_image" |
                              # "author_cover_image" | "management_image" | "ad_image" |
                              # "issue_pdf" | "issue_cover"
    record_id: str
    field: str
    current_reference: str
    expected_local_path: str
    local_exists: bool
    object_key: str
    matbaa_url: str
    already_migrated: bool
    object_exists_in_r2: bool = False
    uploaded_now: bool = False
    would_update: bool = False
    updated: bool = False
    status: str = ""
    note: str = ""


def is_migrated(value):
    return bool(value) and value.startswith(("http://", "https://"))


def content_type_for(ext, is_pdf=False):
    if is_pdf:
        return PDF_CONTENT_TYPE
    return uploads.IMAGE_CONTENT_TYPES.get(ext, "application/octet-stream")


def process_field(media_type, record_id, field_name, local_dir, value, build_key,
                   execute, is_pdf=False):
    """The full locate -> validate -> key -> exists? -> upload -> confirm
    pipeline for one field on one record. Returns a MediaItem describing
    exactly what was found/would happen/happened, and the new value to
    store in the record (or None if the record should not change)."""
    already_migrated = is_migrated(value)
    expected_local_path = os.path.join(local_dir, value) if (value and not already_migrated) else ""
    item = MediaItem(
        media_type=media_type, record_id=record_id, field=field_name,
        current_reference=value or "", expected_local_path=expected_local_path,
        local_exists=bool(expected_local_path) and os.path.exists(expected_local_path),
        object_key="", matbaa_url="", already_migrated=already_migrated,
    )

    if not value:
        item.status = "empty"
        return item, None

    if item.already_migrated:
        item.matbaa_url = value
        item.object_key = storage.key_from_url(value) or ""
        item.status = "already-migrated"
        return item, None

    ext = uploads.ext_of(value)
    allowed = uploads.ALLOWED_PDF_EXT if is_pdf else uploads.ALLOWED_IMAGE_EXT
    if not item.local_exists:
        item.status = "local-file-missing"
        return item, None
    if ext not in allowed:
        item.status = f"unsupported-extension ({ext})"
        return item, None

    object_key = build_key(ext)
    item.object_key = object_key
    item.matbaa_url = storage.public_url(object_key)
    item.object_exists_in_r2 = storage.object_exists(object_key)
    item.would_update = True

    if item.object_exists_in_r2:
        item.status = "object-already-in-r2" if not execute else "linked-existing-object"
        if execute:
            item.updated = True
            return item, item.matbaa_url
        return item, None

    if not execute:
        item.status = "would-upload"
        return item, None

    content_type = content_type_for(ext, is_pdf=is_pdf)
    try:
        with open(expected_local_path, "rb") as f:
            storage.upload_fileobj(f, object_key, content_type=content_type)
    except Exception as exc:
        item.status = f"upload-failed: {exc}"
        return item, None

    if not storage.object_exists(object_key):
        item.status = "upload-verification-failed (object not found in R2 after upload) -- record NOT updated"
        return item, None

    item.uploaded_now = True
    item.updated = True
    item.status = "uploaded"
    return item, item.matbaa_url


def print_item(item):
    print(f"  [{item.status}] {item.media_type} {item.record_id} .{item.field}")
    print(f"      current reference : {item.current_reference or '(empty)'}")
    if item.current_reference and not item.already_migrated:
        print(f"      expected source   : {item.expected_local_path}")
        print(f"      source exists     : {item.local_exists}")
    if item.object_key:
        print(f"      R2 object key     : {item.object_key}")
        print(f"      matbaa URL        : {item.matbaa_url}")
        print(f"      already in R2     : {item.object_exists_in_r2}")
    print(f"      already migrated  : {item.already_migrated}")
    print(f"      would/does update : {item.would_update or item.updated}")


# =========================================================================
# Report
# =========================================================================

class Report:
    def __init__(self):
        self.items = []
        self.scanned = 0
        self.article_images = 0
        self.magazine_images = 0
        self.author_images = 0
        self.management_images = 0
        self.ad_images = 0
        self.newspaper_issues = 0
        self.newspaper_pdfs = 0
        self.newspaper_covers = 0
        self.already_migrated = 0
        self.uploaded = 0
        self.updated = 0
        self.missing = []
        self.failed = []
        self.unresolved = []

    TYPE_BUCKETS = {
        "article_image": "article_images", "magazine_image": "magazine_images",
        "author_profile_image": "author_images", "author_cover_image": "author_images",
        "management_image": "management_images", "ad_image": "ad_images",
        "issue_pdf": "newspaper_pdfs", "issue_cover": "newspaper_covers",
    }

    def count_type(self, media_type):
        bucket = self.TYPE_BUCKETS.get(media_type)
        if bucket:
            setattr(self, bucket, getattr(self, bucket) + 1)

    def add(self, item):
        self.items.append(item)
        self.scanned += 1
        self.count_type(item.media_type)
        if item.already_migrated:
            self.already_migrated += 1
        if item.uploaded_now:
            self.uploaded += 1
        if item.updated:
            self.updated += 1
        if item.status == "local-file-missing":
            self.missing.append(item)
        if item.status.startswith("upload-failed") or item.status.startswith("upload-verification-failed"):
            self.failed.append(item)
        if item.current_reference and not item.already_migrated and not item.updated and item.status != "empty":
            self.unresolved.append(item)

    def print_summary(self, mode):
        bar = "=" * 78
        print(f"\n{bar}\nSUMMARY ({mode})\n{bar}")
        print(f"Total records/fields scanned : {self.scanned}")
        print(f"  Article images              : {self.article_images}")
        print(f"  Arı Magazin images          : {self.magazine_images}")
        print(f"  Author images               : {self.author_images}")
        print(f"  Gazete Yönetimi images      : {self.management_images}")
        print(f"  Ad creative images (info.)  : {self.ad_images}")
        print(f"  Newspaper issues            : {self.newspaper_issues}")
        print(f"  Newspaper PDFs              : {self.newspaper_pdfs}")
        print(f"  Newspaper covers            : {self.newspaper_covers}")
        print(f"Already migrated (skipped)   : {self.already_migrated}")
        print(f"Successfully uploaded        : {self.uploaded}")
        print(f"References updated           : {self.updated}")
        missing_label = "R2 objects missing        " if mode == "verify" else "Local files missing      "
        print(f"{missing_label}    : {len(self.missing)}")
        failed_label = "Broken/foreign URLs        " if mode == "verify" else "Upload failures           "
        print(f"{failed_label}    : {len(self.failed)}")
        print(f"Unresolved legacy references : {len(self.unresolved)}")
        if self.missing:
            print(f"\n-- {missing_label.strip()} --")
            for it in self.missing:
                where = it.matbaa_url if mode == "verify" else it.expected_local_path
                print(f"  ! {it.media_type} {it.record_id} .{it.field}: {where}")
        if self.failed:
            print(f"\n-- {failed_label.strip()} --")
            for it in self.failed:
                print(f"  ! {it.media_type} {it.record_id} .{it.field}: {it.status}")
        if self.unresolved:
            print("\n-- Unresolved legacy references (still a bare local filename) --")
            for it in self.unresolved:
                print(f"  ! {it.media_type} {it.record_id} .{it.field}: {it.current_reference} ({it.status})")
        print(bar)


# =========================================================================
# Per-type scans
# =========================================================================

def scan_articles(report, paths, dirs, execute):
    print("\nArticles (haber + Arı Magazin):")
    articles = read_json(paths["articles"])
    changed = False
    for a in articles:
        slug = a.get("slug") or "(no slug)"
        section = a.get("section")
        is_magazine = section == "magazin"
        media_type = "magazine_image" if is_magazine else "article_image"
        folder = "gorseller/dergi" if is_magazine else "gorseller/haberler"
        value = a.get("image")
        item, new_value = process_field(
            media_type, slug, "image", dirs["img_articles"], value,
            build_key=lambda ext, s=slug, f=folder: f"{f}/{s}.{ext}",
            execute=execute,
        )
        print_item(item)
        report.add(item)
        if new_value:
            a["image"] = new_value
            changed = True
    if changed and execute:
        write_json_safely(paths["articles"], articles, RUN_STAMP)
    return changed


def scan_authors(report, paths, dirs, execute):
    print("\nAuthors:")
    authors = read_json(paths["authors"])
    changed = False
    for author in authors:
        slug = author.get("slug") or author.get("id") or "(no slug)"
        for field_name, media_type in (("profile_image", "author_profile_image"), ("cover_image", "author_cover_image")):
            value = author.get(field_name)
            item, new_value = process_field(
                media_type, slug, field_name, dirs["img_authors"], value,
                build_key=lambda ext, v=value: f"gorseller/yazarlar/{v}",
                execute=execute,
            )
            print_item(item)
            report.add(item)
            if new_value:
                author[field_name] = new_value
                changed = True
    if changed and execute:
        write_json_safely(paths["authors"], authors, RUN_STAMP)
    return changed


def scan_management(report, paths, dirs, execute):
    print("\nGazete Yönetimi (masthead):")
    entries = read_json(paths["management"])
    changed = False
    for entry in entries:
        eid = entry.get("id") or entry.get("display_name") or "(no id)"
        value = entry.get("image_override")
        item, new_value = process_field(
            "management_image", eid, "image_override", dirs["img_management"], value,
            build_key=lambda ext, v=value: f"gorseller/yonetim/{v}",
            execute=execute,
        )
        print_item(item)
        report.add(item)
        if new_value:
            entry["image_override"] = new_value
            changed = True
    if changed and execute:
        write_json_safely(paths["management"], entries, RUN_STAMP)
    return changed


def scan_ads(report, paths, dirs, execute):
    print("\nAdvertisements (informational -- these have only ever been R2-native since\n"
          "the advertising feature shipped after R2 support existed, so this is\n"
          "normally empty; included for completeness per 'any other legacy public media'):")
    ads = read_json(paths["ads"])
    changed = False
    for ad in ads:
        aid = ad.get("id") or "(no id)"
        value = ad.get("image")
        item, new_value = process_field(
            "ad_image", aid, "image", dirs["img_ads"], value,
            build_key=lambda ext, v=value: f"gorseller/reklamlar/{v}",
            execute=execute,
        )
        print_item(item)
        report.add(item)
        if new_value:
            ad["image"] = new_value
            changed = True
    if changed and execute:
        write_json_safely(paths["ads"], ads, RUN_STAMP)
    return changed


def scan_issues(report, paths, dirs, execute):
    print("\nNewspaper issues (PDF + cover) -- the full archive:")
    issues = read_json(paths["issues"])
    changed = False
    archive_rows = []
    for issue in issues:
        report.newspaper_issues += 1
        iid = issue.get("id") or "(no id)"
        year, month = uploads.issue_date_parts(issue.get("date"))

        pdf_value = issue.get("pdf")
        pdf_item, new_pdf = process_field(
            "issue_pdf", iid, "pdf", dirs["issues"], pdf_value,
            build_key=lambda ext, y=year, m=month, v=pdf_value: f"gazeteler/{y:04d}/{m:02d}/{v}",
            execute=execute, is_pdf=True,
        )
        print_item(pdf_item)
        report.add(pdf_item)
        if new_pdf:
            issue["pdf"] = new_pdf
            changed = True

        cover_value = issue.get("cover_image")
        cover_item, new_cover = process_field(
            "issue_cover", iid, "cover_image", dirs["img_articles"], cover_value,
            build_key=lambda ext, y=year, m=month, v=cover_value: f"gazeteler/{y:04d}/{m:02d}/{v}",
            execute=execute,
        )
        print_item(cover_item)
        report.add(cover_item)
        if new_cover:
            issue["cover_image"] = new_cover
            changed = True

        archive_rows.append((iid, pdf_item, cover_item))

    if changed and execute:
        write_json_safely(paths["issues"], issues, RUN_STAMP)

    print_newspaper_archive_table(archive_rows)
    return changed


def print_newspaper_archive_table(rows):
    bar = "-" * 78
    print(f"\n{bar}\nNEWSPAPER ARCHIVE -- issue by issue\n{bar}")
    if not rows:
        print("  (no issues found in issues.json)")
    for iid, pdf_item, cover_item in rows:
        def describe(it):
            src = it.expected_local_path or "(none)"
            dst = it.matbaa_url or it.current_reference or "(none)"
            return src, dst, it.status
        pdf_src, pdf_dst, pdf_status = describe(pdf_item)
        cov_src, cov_dst, cov_status = describe(cover_item)
        print(f"\n  Issue     : {iid}")
        print(f"    PDF")
        print(f"      Source      : {pdf_src}")
        print(f"      Destination : {pdf_dst}")
        print(f"      Status      : {pdf_status}")
        print(f"    Cover")
        print(f"      Source      : {cov_src}")
        print(f"      Destination : {cov_dst}")
        print(f"      Status      : {cov_status}")
    print(bar)


# =========================================================================
# Verify mode -- confirms what's already migrated actually resolves, and
# that the entire newspaper archive is intact. Makes no changes.
# =========================================================================

def verify_field(report, media_type, record_id, field_name, value):
    """Returns True if this field is fully OK (empty is considered OK --
    nothing to verify), False if it's missing/broken/still-legacy. Used by
    the caller to decide per-issue archive completeness."""
    report.scanned += 1
    report.count_type(media_type)
    if not value:
        return True
    if not is_migrated(value):
        item = MediaItem(
            media_type=media_type, record_id=record_id, field=field_name,
            current_reference=value, expected_local_path="", local_exists=False,
            object_key="", matbaa_url="", already_migrated=False, status="still-legacy",
        )
        report.unresolved.append(item)
        print(f"  [still-legacy] {media_type} {record_id} .{field_name} -- {value}")
        return False
    key = storage.key_from_url(value)
    if not key:
        item = MediaItem(
            media_type=media_type, record_id=record_id, field=field_name,
            current_reference=value, expected_local_path="", local_exists=False,
            object_key="", matbaa_url=value, already_migrated=True,
            status="foreign-url (does not match R2_PUBLIC_BASE_URL)",
        )
        report.failed.append(item)
        print(f"  [foreign-url] {media_type} {record_id} .{field_name} -- {value}")
        return False
    exists = storage.object_exists(key)
    item = MediaItem(
        media_type=media_type, record_id=record_id, field=field_name,
        current_reference=value, expected_local_path="", local_exists=False,
        object_key=key, matbaa_url=value, already_migrated=True,
        object_exists_in_r2=exists, status="verified-ok" if exists else "verified-missing",
    )
    report.items.append(item)
    if exists:
        report.already_migrated += 1
    else:
        report.missing.append(item)
    print(f"  [{item.status}] {media_type} {record_id} .{field_name} -- {value}")
    return exists


def run_verify(paths, dirs):
    if not storage.ENABLED:
        print("\nR2 is not configured -- set R2_* environment variables before verifying.")
        sys.exit(1)

    report = Report()

    print("\nArticles:")
    for a in read_json(paths["articles"]):
        media_type = "magazine_image" if a.get("section") == "magazin" else "article_image"
        verify_field(report, media_type, a.get("slug", "?"), "image", a.get("image"))

    print("\nAuthors:")
    for author in read_json(paths["authors"]):
        slug = author.get("slug", "?")
        verify_field(report, "author_profile_image", slug, "profile_image", author.get("profile_image"))
        verify_field(report, "author_cover_image", slug, "cover_image", author.get("cover_image"))

    print("\nGazete Yönetimi:")
    for entry in read_json(paths["management"]):
        verify_field(report, "management_image", entry.get("id", "?"), "image_override", entry.get("image_override"))

    print("\nAdvertisements:")
    for ad in read_json(paths["ads"]):
        verify_field(report, "ad_image", ad.get("id", "?"), "image", ad.get("image"))

    print("\nNewspaper issues -- every issue's PDF and cover must resolve:")
    issues = read_json(paths["issues"])
    incomplete_issues = []
    for issue in issues:
        report.newspaper_issues += 1
        iid = issue.get("id", "?")
        pdf_ok = verify_field(report, "issue_pdf", iid, "pdf", issue.get("pdf"))
        cov_ok = verify_field(report, "issue_cover", iid, "cover_image", issue.get("cover_image"))
        if not (pdf_ok and cov_ok):
            incomplete_issues.append(iid)
    all_ok = not incomplete_issues

    report.print_summary("verify")
    if not issues:
        print("\nEvery known newspaper issue fully located: N/A (no issues in issues.json)")
    elif all_ok:
        print(f"\nEvery known newspaper issue fully located: YES ({len(issues)} issue(s) checked)")
    else:
        print(f"\nEvery known newspaper issue fully located: NO -- incomplete: {', '.join(incomplete_issues)}")

    if report.missing or report.failed:
        sys.exit(2)


# =========================================================================
# main
# =========================================================================

RUN_STAMP = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")


def resolve_paths_and_dirs():
    """Runs path detection, prints its report (always -- independent of
    whether R2 is configured, since it answers an infrastructure question
    the operator needs regardless), and returns (paths, dirs) ready for
    the scan_*()/run_verify() functions."""
    resolved, infos = detect_paths()
    print_path_report(resolved, infos)
    paths = data_paths(resolved["data"])
    dirs = {
        "img_articles": resolved["img_articles"],
        "img_authors": resolved["img_authors"],
        "issues": resolved["issues"],
        "img_management": resolved["img_management"],
        "img_ads": resolved["img_ads"],
    }
    return paths, dirs


def run_migration(execute):
    paths, dirs = resolve_paths_and_dirs()

    if not storage.ENABLED:
        print(
            "\nR2 is not configured (need R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / "
            "R2_SECRET_ACCESS_KEY / R2_BUCKET_NAME / R2_PUBLIC_BASE_URL). "
            "Set these first -- see .env.example. This applies to dry runs too: "
            "reporting an accurate 'already migrated' / 'would upload' status "
            "requires being able to ask R2 what's already there."
        )
        sys.exit(1)

    print(f"\nTarget bucket   : {storage.R2_BUCKET_NAME}")
    print(f"Public base URL : {storage.R2_PUBLIC_BASE_URL}")
    print("Mode            : " + ("EXECUTE (will upload + rewrite records)" if execute else "DRY RUN (zero changes)"))

    report = Report()
    scan_articles(report, paths, dirs, execute)
    scan_authors(report, paths, dirs, execute)
    scan_management(report, paths, dirs, execute)
    scan_ads(report, paths, dirs, execute)
    scan_issues(report, paths, dirs, execute)

    report.print_summary("execute" if execute else "dry-run")

    if execute and report.failed:
        print(f"\n{len(report.failed)} object(s) failed. Their JSON records were left untouched. "
              "It is safe to run this script again -- already-uploaded/already-updated items "
              "are skipped, and only the remaining failures will be retried.")
        sys.exit(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--execute", action="store_true",
                        help="Actually upload to R2 and rewrite the JSON records (default: dry run only).")
    group.add_argument("--verify", action="store_true",
                        help="Check already-migrated records + the full newspaper archive against R2. Makes no changes.")
    args = parser.parse_args()

    if args.verify:
        paths, dirs = resolve_paths_and_dirs()
        run_verify(paths, dirs)
    else:
        run_migration(execute=args.execute)


if __name__ == "__main__":
    main()
