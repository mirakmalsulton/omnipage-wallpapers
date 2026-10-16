#!/usr/bin/env python3
"""
build.py — сжимает оригиналы из wallpapers-src/ в WebP (full + thumb)
          и генерит index.json. Запускается из корня omnipage-wallpapers.

Использование:
    python3 tools/build.py              # пересобрать (incremental)
    python3 tools/build.py --force      # пересжать всё заново
    python3 tools/build.py --push       # + git commit + push
    python3 tools/build.py --dry-run    # показать что будет сделано

Структура (всё внутри omnipage-wallpapers/):
    wallpapers-src/<category>/<name>.{jpg,png}  ← оригиналы (в .gitignore)
    <category>/<name>.webp                       ← full
    <category>/<name>_t.webp                     ← thumb
    index.json                                   ← автогенерация
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps

# ── Настройки ──────────────────────────────────────────────────────────────
GITHUB_USER   = "mirakmalsulton"
GITHUB_REPO   = "omnipage-wallpapers"
GITHUB_BRANCH = "main"

# jsDelivr base — используется в расширении для построения абсолютных URL
CDN_BASE_URL  = f"https://cdn.jsdelivr.net/gh/{GITHUB_USER}/{GITHUB_REPO}@{GITHUB_BRANCH}/"

# Параметры сжатия
FULL_SIZE     = (1920, 1080)   # fit — обрезка под пропорцию
FULL_QUALITY  = 75
THUMB_SIZE    = (400, 250)     # 16:10, похож на UI-карточку
THUMB_QUALITY = 55

# Пути относительно корня omnipage-wallpapers репо (где и лежит этот скрипт).
ROOT          = Path(__file__).resolve().parent.parent
SRC_DIR       = ROOT / "wallpapers-src"
OUT_DIR       = ROOT

# Форматы оригиналов
VALID_EXTS    = {".jpg", ".jpeg", ".png"}

INDEX_VERSION = 1


# ── Утилиты ────────────────────────────────────────────────────────────────

def human_name(slug: str) -> str:
    """nature → Nature, pure-dark → Pure Dark, city_night → City Night."""
    return re.sub(r"[-_]+", " ", slug).strip().title()


def thumb_path(full: Path) -> Path:
    """aurora.webp → aurora_t.webp (в той же папке)."""
    return full.with_name(full.stem + "_t.webp")


def is_up_to_date(src: Path, dst: Path) -> bool:
    """True если dst существует и свежее исходника."""
    return dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime


def compress(src: Path, dst: Path, size, quality: int) -> int:
    """Сжать src в webp по размеру size (fit, обрезка). Вернуть размер в байтах."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        # fit — вписывает по короткой стороне и обрезает лишнее, сохраняя пропорции
        im = ImageOps.fit(im, size, method=Image.LANCZOS, centering=(0.5, 0.5))
        if im.mode == "RGBA":
            im = im.convert("RGB")
        im.save(dst, format="WEBP", quality=quality, method=6)
    return dst.stat().st_size


def fmt_kb(n: int) -> str:
    return f"{n / 1024:.1f} KB"


# ── Основная логика ────────────────────────────────────────────────────────

def scan_sources():
    """[(category_slug, name_slug, src_path), ...] — отсортировано."""
    if not SRC_DIR.exists():
        return []
    items = []
    for cat_dir in sorted(SRC_DIR.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name.startswith("."):
            continue
        for file in sorted(cat_dir.iterdir()):
            if file.is_file() and file.suffix.lower() in VALID_EXTS:
                items.append((cat_dir.name, file.stem, file))
    return items


def build(force: bool, dry_run: bool) -> dict:
    """Основной проход: сжатие + сборка модели для index.json."""
    sources = scan_sources()
    categories = {}  # slug → {"name", "items": [...]}

    built = skipped = 0
    total_bytes = 0

    for cat_slug, name_slug, src in sources:
        cat = categories.setdefault(cat_slug, {"name": human_name(cat_slug), "items": []})

        full_rel  = f"{cat_slug}/{name_slug}.webp"
        thumb_rel = f"{cat_slug}/{name_slug}_t.webp"
        full_dst  = OUT_DIR / full_rel
        thumb_dst = OUT_DIR / thumb_rel

        need_full  = force or not is_up_to_date(src, full_dst)
        need_thumb = force or not is_up_to_date(src, thumb_dst)

        if dry_run:
            marker = " [to build]" if (need_full or need_thumb) else " [up-to-date]"
            print(f"  {cat_slug}/{name_slug}{marker}")
        else:
            if need_full:
                size = compress(src, full_dst, FULL_SIZE, FULL_QUALITY)
                print(f"  ✓ {full_rel} ({fmt_kb(size)})")
                total_bytes += size
                built += 1
            else:
                skipped += 1
            if need_thumb:
                size = compress(src, thumb_dst, THUMB_SIZE, THUMB_QUALITY)
                print(f"  ✓ {thumb_rel} ({fmt_kb(size)})")
                total_bytes += size

        cat["items"].append({
            "id":    name_slug,
            "name":  human_name(name_slug),
            "thumb": thumb_rel,
            "full":  full_rel,
        })

    # Чистка: удаляем из OUT_DIR webp-ы, для которых нет исходника
    if not dry_run:
        removed = clean_orphans(sources)
        if removed:
            print(f"\n🗑  Removed {removed} orphan webp(s)")

    print(f"\nBuilt: {built}, skipped: {skipped}, total written: {fmt_kb(total_bytes)}")

    return {
        "version":     INDEX_VERSION,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "baseUrl":     CDN_BASE_URL,
        "categories":  [
            {"id": slug, "name": data["name"], "items": data["items"]}
            for slug, data in sorted(categories.items())
        ],
    }


RESERVED_DIRS = {".git", "tools", "wallpapers-src", ".github"}

def clean_orphans(sources) -> int:
    """Удалить webp-ы, для которых нет src-исходника. И пустые категории."""
    valid = set()
    for cat, name, _ in sources:
        valid.add(f"{cat}/{name}.webp")
        valid.add(f"{cat}/{name}_t.webp")

    removed = 0
    if not OUT_DIR.exists():
        return 0

    # Проходим только по категорийным папкам, исключая служебные
    for cat_dir in OUT_DIR.iterdir():
        if not cat_dir.is_dir() or cat_dir.name in RESERVED_DIRS or cat_dir.name.startswith("."):
            continue
        for item in cat_dir.rglob("*.webp"):
            rel = item.relative_to(OUT_DIR).as_posix()
            if rel not in valid:
                item.unlink()
                removed += 1

    # Удалить пустые категории
    for cat_dir in OUT_DIR.iterdir():
        if cat_dir.is_dir() and cat_dir.name not in RESERVED_DIRS and not cat_dir.name.startswith(".") and not any(cat_dir.iterdir()):
            cat_dir.rmdir()

    return removed


def write_index(index: dict, dry_run: bool):
    out = OUT_DIR / "index.json"
    text = json.dumps(index, ensure_ascii=False, indent=2) + "\n"
    if dry_run:
        print(f"\n[dry-run] Would write {out}")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"\n✓ Wrote {out.relative_to(OUT_DIR.parent)}")


def git_push():
    """git add + commit + push в OUT_DIR."""
    if not (OUT_DIR / ".git").exists():
        print(f"\n✗ {OUT_DIR} не является git-репо. Склонируй сначала:")
        print(f"    git clone git@github.com:{GITHUB_USER}/{GITHUB_REPO}.git {OUT_DIR}")
        sys.exit(1)

    # Проверим есть ли что коммитить
    status = subprocess.run(
        ["git", "-C", str(OUT_DIR), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    )
    if not status.stdout.strip():
        print("\nNo changes to push.")
        return

    msg = f"chore: обновление обоев ({datetime.now().strftime('%Y-%m-%d %H:%M')})"
    subprocess.run(["git", "-C", str(OUT_DIR), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(OUT_DIR), "commit", "-m", msg], check=True)
    subprocess.run(["git", "-C", str(OUT_DIR), "push"], check=True)
    print(f"\n✓ Pushed to {GITHUB_USER}/{GITHUB_REPO}@{GITHUB_BRANCH}")


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build OmniPage wallpapers")
    parser.add_argument("--force",   action="store_true", help="пересжать всё заново")
    parser.add_argument("--push",    action="store_true", help="git commit+push после сборки")
    parser.add_argument("--dry-run", action="store_true", help="только показать что будет")
    args = parser.parse_args()

    print(f"Source: {SRC_DIR}")
    print(f"Output: {OUT_DIR}")
    print(f"CDN:    {CDN_BASE_URL}\n")

    if not SRC_DIR.exists():
        print(f"⚠  {SRC_DIR} не существует. Создай и добавь картинки:")
        print(f"    mkdir -p {SRC_DIR}/nature && cp *.jpg {SRC_DIR}/nature/")
        sys.exit(1)

    sources = scan_sources()
    if not sources:
        print("⚠  Нет картинок в wallpapers-src/. Нечего собирать.")
        # Всё равно пишем пустой index.json, чтобы расширение не падало
        if not args.dry_run:
            write_index({
                "version":     INDEX_VERSION,
                "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "baseUrl":     CDN_BASE_URL,
                "categories":  [],
            }, args.dry_run)
        sys.exit(0)

    print(f"Found {len(sources)} source image(s):\n")
    index = build(args.force, args.dry_run)
    write_index(index, args.dry_run)

    if args.push:
        if args.dry_run:
            print("\n[dry-run] Would git add/commit/push")
        else:
            git_push()


if __name__ == "__main__":
    main()