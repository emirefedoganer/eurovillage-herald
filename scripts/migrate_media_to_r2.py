#!/usr/bin/env python3
"""Migrates existing local Herald media (article/Arı Magazin images, author
profile/cover images, newspaper cover images, newspaper PDF editions) to
Cloudflare R2, and rewrites the JSON records that reference them to point
at the resulting https://matbaa.eurovillageherald.com URLs.

Safe by construction:
  - DRY RUN IS THE DEFAULT. Nothing is uploaded and nothing is written
    unless you pass --execute.
  - Idempotent: a record whose value already points at R2 is left alone
    (counted as "already migrated"), so running this repeatedly -- or
    resuming after a partial run -- never re-uploads or re-writes it.
  - Every JSON file this touches is backed up (timestamped copy) before
    the first write of an --execute run.
  - Local files are NEVER deleted by this script, under any mode.
  - Per object: verify the source file exists -> validate its type ->
    determine Content-Type -> generate a stable key -> upload -> confirm
    the upload actually landed (HEAD request) -> only THEN update the
    stored reference. If any step fails, that one object is recorded as
    failed and the script moves on -- it never replaces a working local
    reference with a broken URL, and one failure never aborts the run.

Usage (run from the repository root):
    python3 -m pip install -r requirements.txt      # needs boto3
    # Set R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY /
    # R2_BUCKET_NAME / R2_PUBLIC_BASE_URL first -- see .env.example.

    python3 scripts/migrate_media_to_r2.py                # dry run (default)
    python3 scripts/migrate_media_to_r2.py --execute       # actually migrate
    python3 scripts/migrate_media_to_r2.py --verify         # check R2 vs. records
"""
import argparse
import os
import shutil
import sys
from datetime import datetime

APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
sys.path.insert(0, APP_DIR)

import store  # noqa: E402
import storage  # noqa: E402
import uploads  # noqa: E402

ARTICLE_IMG_DIR = os.path.join(APP_DIR, "static", "img", "articles")
AUTHOR_IMG_DIR = os.path.join(APP_DIR, "static", "img", "authors")
ISSUE_PDF_DIR = os.path.join(APP_DIR, "static", "issues")

PDF_CONTENT_TYPE = "application/pdf"


# --------------------------------------------------------------- helpers --

def is_migrated(value):
    return bool(value) and value.startswith(("http://", "https://"))


def content_type_for(ext, is_pdf=False):
    if is_pdf:
        return PDF_CONTENT_TYPE
    return uploads.IMAGE_CONTENT_TYPES.get(ext, "application/octet-stream")


class Report:
    """Accumulates every counter the spec asks the final report to show,
    for whichever mode is running (dry-run / execute / verify)."""

    def __init__(self):
        self.scanned = 0
        self.local_files_found = 0
        self.uploaded = 0
        self.updated = 0
        self.already_migrated = 0
        self.missing_files = []
        self.failed = []
        self.skipped = []
        self.unchanged = 0
        # --verify only
        self.verified_ok = []
        self.verified_missing = []
        self.verified_inaccessible = []
        self.still_legacy = []

    def print_summary(self, mode):
        bar = "=" * 70
        print(f"\n{bar}\nÖZET ({mode})\n{bar}")
        print(f"Taranan kayıt/alan sayısı:      {self.scanned}")
        if mode != "verify":
            print(f"Bulunan yerel dosya:            {self.local_files_found}")
            print(f"Yüklenen dosya:                 {self.uploaded}")
            print(f"Güncellenen kayıt:              {self.updated}")
            print(f"Zaten taşınmış (atlanan):       {self.already_migrated}")
            print(f"Eksik dosya:                    {len(self.missing_files)}")
            print(f"Başarısız yükleme:              {len(self.failed)}")
            print(f"Boş/uygulanamaz (atlanan):      {len(self.skipped)}")
            print(f"Değişmeden kalan:               {self.unchanged}")
            if self.missing_files:
                print("\n-- Eksik dosyalar --")
                for line in self.missing_files:
                    print(f"  ! {line}")
            if self.failed:
                print("\n-- Başarısız yüklemeler --")
                for line in self.failed:
                    print(f"  ! {line}")
        else:
            print(f"Geçerli (R2'de doğrulandı):     {len(self.verified_ok)}")
            print(f"Eksik (R2'de bulunamadı):       {len(self.verified_missing)}")
            print(f"Erişilemez / matbaa dışı URL:   {len(self.verified_inaccessible)}")
            print(f"Hâlâ yerel medyaya işaret eden: {len(self.still_legacy)}")
            if self.verified_missing:
                print("\n-- R2'de bulunamayan (kırık) referanslar --")
                for line in self.verified_missing:
                    print(f"  ! {line}")
            if self.verified_inaccessible:
                print("\n-- matbaa.eurovillageherald.com dışına işaret edenler --")
                for line in self.verified_inaccessible:
                    print(f"  ! {line}")
            if self.still_legacy:
                print("\n-- Hâlâ yerel dosyaya işaret eden kayıtlar --")
                for line in self.still_legacy:
                    print(f"  ! {line}")
        print(bar)


def backup_data_files():
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    data_dir = os.path.join(APP_DIR, "data")
    backup_dir = os.path.join(data_dir, f".backup-{stamp}")
    os.makedirs(backup_dir, exist_ok=True)
    for fname in ("articles.json", "authors.json", "issues.json"):
        src = os.path.join(data_dir, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(backup_dir, fname))
    print(f"Yedek alındı: {backup_dir}\n")
    return backup_dir


# ------------------------------------------------------- per-field logic --

def migrate_field(report, execute, kind_label, record_label, local_dir,
                   value, build_key, is_pdf=False):
    """Runs the full verify -> validate -> key -> upload -> confirm ->
    (caller updates reference) pipeline for one field on one record.
    Returns the new value to store, or None if nothing should change."""
    report.scanned += 1

    if not value:
        report.skipped.append(f"{kind_label} {record_label}: alan boş")
        return None

    if is_migrated(value):
        report.already_migrated += 1
        return None

    local_path = os.path.join(local_dir, value)
    if not os.path.exists(local_path):
        report.missing_files.append(f"{kind_label} {record_label}: yerel dosya yok -- {value}")
        return None

    ext = uploads.ext_of(value)
    allowed = uploads.ALLOWED_PDF_EXT if is_pdf else uploads.ALLOWED_IMAGE_EXT
    if ext not in allowed:
        report.skipped.append(f"{kind_label} {record_label}: desteklenmeyen dosya türü -- {value}")
        return None

    report.local_files_found += 1
    object_key = build_key(ext)
    public_url = storage.public_url(object_key)
    content_type = content_type_for(ext, is_pdf=is_pdf)

    action = "YÜKLENECEK" if execute else "ÖNİZLEME (dry-run)"
    print(f"  [{action}] {kind_label} {record_label}")
    print(f"      kaynak dosya : {local_path}")
    print(f"      R2 anahtarı  : {object_key}")
    print(f"      genel URL    : {public_url}")

    if not execute:
        return None

    try:
        with open(local_path, "rb") as f:
            uploaded_url = storage.upload_fileobj(f, object_key, content_type=content_type)
    except Exception as exc:
        report.failed.append(f"{kind_label} {record_label}: yükleme hatası -- {exc}")
        return None

    if not storage.object_exists(object_key):
        report.failed.append(
            f"{kind_label} {record_label}: yükleme sonrası doğrulama başarısız "
            f"(nesne R2'de bulunamadı) -- kayıt DEĞİŞTİRİLMEDİ"
        )
        return None

    report.uploaded += 1
    return uploaded_url


# ------------------------------------------------------------- articles --

def migrate_articles(report, execute):
    articles = store.load_articles()
    changed = False
    for a in articles:
        label = f"[{a.get('slug')}]"
        new_url = migrate_field(
            report, execute, "Makale görseli", label, ARTICLE_IMG_DIR, a.get("image"),
            build_key=lambda ext, a=a: (
                f"{'gorseller/dergi' if a.get('section') == 'magazin' else 'gorseller/haberler'}/{a['slug']}.{ext}"
            ),
        )
        if new_url:
            a["image"] = new_url
            report.updated += 1
            changed = True
        elif a.get("image"):
            report.unchanged += 1
    if changed and execute:
        store.save_articles(articles)
    return changed


# --------------------------------------------------------------- authors --

def migrate_authors(report, execute):
    authors = store.load_authors()
    changed = False
    for author in authors:
        for field in ("profile_image", "cover_image"):
            label = f"[{author.get('slug')}] .{field}"
            value = author.get(field)
            new_url = migrate_field(
                report, execute, "Yazar görseli", label, AUTHOR_IMG_DIR, value,
                build_key=lambda ext, value=value: f"gorseller/yazarlar/{value}",
            )
            if new_url:
                author[field] = new_url
                report.updated += 1
                changed = True
            elif value:
                report.unchanged += 1
    if changed and execute:
        store.save_authors(authors)
    return changed


# ---------------------------------------------------------------- issues --

def migrate_issues(report, execute):
    issues = store.load_issues()
    changed = False
    for issue in issues:
        year, month = uploads.issue_date_parts(issue.get("date"))
        label = f"[{issue.get('id')}]"

        pdf_value = issue.get("pdf")
        new_pdf = migrate_field(
            report, execute, "Gazete PDF", f"{label} .pdf", ISSUE_PDF_DIR, pdf_value,
            build_key=lambda ext, y=year, m=month, v=pdf_value: f"gazeteler/{y:04d}/{m:02d}/{v}",
            is_pdf=True,
        )
        if new_pdf:
            issue["pdf"] = new_pdf
            report.updated += 1
            changed = True
        elif pdf_value:
            report.unchanged += 1

        cover_value = issue.get("cover_image")
        new_cover = migrate_field(
            report, execute, "Gazete kapağı", f"{label} .cover_image", ARTICLE_IMG_DIR, cover_value,
            build_key=lambda ext, y=year, m=month, v=cover_value: f"gazeteler/{y:04d}/{m:02d}/{v}",
        )
        if new_cover:
            issue["cover_image"] = new_cover
            report.updated += 1
            changed = True
        elif cover_value:
            report.unchanged += 1
    if changed and execute:
        store.save_issues(issues)
    return changed


# ------------------------------------------------------------- --verify --

def verify_field(report, kind_label, record_label, value):
    if not value:
        return
    if not is_migrated(value):
        report.still_legacy.append(f"{kind_label} {record_label}: hâlâ yerel -- {value}")
        return
    key = storage.key_from_url(value)
    if not key:
        report.verified_inaccessible.append(
            f"{kind_label} {record_label}: matbaa.eurovillageherald.com dışında bir URL -- {value}"
        )
        return
    report.scanned += 1
    if storage.object_exists(key):
        report.verified_ok.append(f"{kind_label} {record_label}: OK -- {value}")
    else:
        report.verified_missing.append(f"{kind_label} {record_label}: R2'de bulunamadı -- {value}")


def run_verify():
    if not storage.ENABLED:
        print("R2 yapılandırılmamış -- doğrulama için önce R2_* ortam değişkenlerini ayarlayın.")
        sys.exit(1)

    report = Report()
    for a in store.load_articles():
        verify_field(report, "Makale görseli", f"[{a.get('slug')}]", a.get("image"))
    for author in store.load_authors():
        verify_field(report, "Yazar görseli", f"[{author.get('slug')}] .profile_image", author.get("profile_image"))
        verify_field(report, "Yazar görseli", f"[{author.get('slug')}] .cover_image", author.get("cover_image"))
    for issue in store.load_issues():
        verify_field(report, "Gazete PDF", f"[{issue.get('id')}] .pdf", issue.get("pdf"))
        verify_field(report, "Gazete kapağı", f"[{issue.get('id')}] .cover_image", issue.get("cover_image"))

    report.print_summary("verify")
    if report.verified_missing or report.verified_inaccessible:
        sys.exit(2)


# ------------------------------------------------------------------ main --

def run_migration(execute):
    if not storage.ENABLED:
        print(
            "R2 yapılandırılmamış (R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / "
            "R2_SECRET_ACCESS_KEY / R2_BUCKET_NAME / R2_PUBLIC_BASE_URL). "
            "Bunları önce ayarlayın -- bkz. .env.example."
        )
        sys.exit(1)

    if execute:
        backup_data_files()
    else:
        print("(dry run -- hiçbir dosya yüklenmeyecek, hiçbir kayıt değiştirilmeyecek)\n")

    report = Report()

    print("Makaleler (haberler + Arı Magazin):")
    migrate_articles(report, execute)
    print("\nYazarlar:")
    migrate_authors(report, execute)
    print("\nGazete Sayıları (PDF + kapak):")
    migrate_issues(report, execute)

    report.print_summary("execute" if execute else "dry-run")

    if execute and report.failed:
        print(f"\n{len(report.failed)} nesne yüklenemedi -- bu betiği tekrar çalıştırmak güvenlidir, "
              "yalnızca eksik/başarısız olanlar yeniden denenecektir.")
        sys.exit(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--execute", action="store_true",
                        help="Actually upload and rewrite records (default: dry run only).")
    group.add_argument("--verify", action="store_true",
                        help="Check already-migrated (R2) references against the bucket; makes no changes.")
    args = parser.parse_args()

    if args.verify:
        run_verify()
    else:
        run_migration(execute=args.execute)


if __name__ == "__main__":
    main()
