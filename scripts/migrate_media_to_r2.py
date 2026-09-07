#!/usr/bin/env python3
"""One-time migration: upload existing local media (article images, author
images, newspaper PDF editions + covers) to Cloudflare R2, and rewrite the
JSON records to point at the new matbaa.eurovillageherald.com URLs.

Safe to run more than once -- any record whose value is already a full
http(s) URL is skipped, so a partially-completed run can simply be
re-run. Local files are never deleted by this script.

Usage:
    cd eurovillage-herald
    python3 -m pip install -r requirements.txt   # needs boto3
    # Set R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY /
    # R2_BUCKET_NAME / R2_PUBLIC_BASE_URL first (see .env.example).
    python3 scripts/migrate_media_to_r2.py --dry-run   # preview only
    python3 scripts/migrate_media_to_r2.py             # actually upload + rewrite

A timestamped backup of each JSON file touched is written next to it
before any changes are saved.
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


def is_migrated(value):
    return bool(value) and value.startswith(("http://", "https://"))


def upload_local_file(local_path, object_key, content_type):
    with open(local_path, "rb") as f:
        return storage.upload_fileobj(f, object_key, content_type=content_type)


def migrate_articles(dry_run):
    articles = store.load_articles()
    changed = 0
    for article in articles:
        image = article.get("image")
        if not image or is_migrated(image):
            continue
        local_path = os.path.join(ARTICLE_IMG_DIR, image)
        if not os.path.exists(local_path):
            print(f"  [skip] articles.json: {article['slug']} -- local file not found: {image}")
            continue
        ext = uploads.ext_of(image)
        folder = "gorseller/dergi" if article.get("section") == "magazin" else "gorseller/haberler"
        object_key = f"{folder}/{article['slug']}.{ext}"
        print(f"  {article['slug']}: {image} -> {object_key}")
        changed += 1
        if not dry_run:
            url = upload_local_file(local_path, object_key, uploads.IMAGE_CONTENT_TYPES.get(ext))
            article["image"] = url
    if changed and not dry_run:
        store.save_articles(articles)
    return changed


def migrate_authors(dry_run):
    authors = store.load_authors()
    changed = 0
    for author in authors:
        for field in ("profile_image", "cover_image"):
            value = author.get(field)
            if not value or is_migrated(value):
                continue
            local_path = os.path.join(AUTHOR_IMG_DIR, value)
            if not os.path.exists(local_path):
                print(f"  [skip] authors.json: {author['slug']} {field} -- local file not found: {value}")
                continue
            object_key = f"gorseller/yazarlar/{value}"
            print(f"  {author['slug']} {field}: {value} -> {object_key}")
            changed += 1
            if not dry_run:
                ext = uploads.ext_of(value)
                url = upload_local_file(local_path, object_key, uploads.IMAGE_CONTENT_TYPES.get(ext))
                author[field] = url
    if changed and not dry_run:
        store.save_authors(authors)
    return changed


def migrate_issues(dry_run):
    issues = store.load_issues()
    changed = 0
    for issue in issues:
        year, month = uploads.issue_date_parts(issue.get("date"))

        pdf = issue.get("pdf")
        if pdf and not is_migrated(pdf):
            local_path = os.path.join(ISSUE_PDF_DIR, pdf)
            if not os.path.exists(local_path):
                print(f"  [skip] issues.json: {issue['id']} pdf -- local file not found: {pdf}")
            else:
                object_key = f"gazeteler/{year:04d}/{month:02d}/{pdf}"
                print(f"  {issue['id']} pdf: {pdf} -> {object_key}")
                changed += 1
                if not dry_run:
                    url = upload_local_file(local_path, object_key, "application/pdf")
                    issue["pdf"] = url

        cover = issue.get("cover_image")
        if cover and not is_migrated(cover):
            local_path = os.path.join(ARTICLE_IMG_DIR, cover)
            if not os.path.exists(local_path):
                print(f"  [skip] issues.json: {issue['id']} cover_image -- local file not found: {cover}")
            else:
                ext = uploads.ext_of(cover)
                object_key = f"gazeteler/{year:04d}/{month:02d}/{cover}"
                print(f"  {issue['id']} cover_image: {cover} -> {object_key}")
                changed += 1
                if not dry_run:
                    url = upload_local_file(local_path, object_key, uploads.IMAGE_CONTENT_TYPES.get(ext))
                    issue["cover_image"] = url
    if changed and not dry_run:
        store.save_issues(issues)
    return changed


def backup_data_files():
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    data_dir = os.path.join(APP_DIR, "data")
    backup_dir = os.path.join(data_dir, f".backup-{stamp}")
    os.makedirs(backup_dir, exist_ok=True)
    for fname in ("articles.json", "authors.json", "issues.json"):
        src = os.path.join(data_dir, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(backup_dir, fname))
    print(f"Backed up articles/authors/issues JSON to {backup_dir}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview without uploading or writing anything.")
    args = parser.parse_args()

    if not storage.ENABLED:
        print("R2 is not configured (R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY/"
              "R2_BUCKET_NAME/R2_PUBLIC_BASE_URL). Set these first -- see .env.example.")
        sys.exit(1)

    if not args.dry_run:
        backup_data_files()
    else:
        print("(dry run -- no files will be uploaded or modified)")

    print("\nArticles:")
    n1 = migrate_articles(args.dry_run)
    print("\nAuthors:")
    n2 = migrate_authors(args.dry_run)
    print("\nIssues:")
    n3 = migrate_issues(args.dry_run)

    total = n1 + n2 + n3
    if args.dry_run:
        print(f"\nDry run complete. Would migrate {total} file(s) -- re-run without --dry-run to apply.")
    else:
        print(f"\nDone. Migrated {total} file(s) to R2 and updated the JSON records.")


if __name__ == "__main__":
    main()
