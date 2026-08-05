#!/usr/bin/env python3
"""Инвентаризация выгрузки Telegram (HTML-экспорт) — цифры для Приложения А в ТЗ.

Считает по ChatExport_*/messages*.html: сообщения, авторов, активность по дням
и часам, ответы/пересылки, медиа по типам, недоступные вложения, домены ссылок.

Это измерительный инструмент, а не парсер корпуса: нормализованный JSONL
делается отдельным скриптом на Ф1 (см. ТЗ, п. 4).

Запуск:  python3 scripts/inventory_stats.py [путь_к_экспорту]
"""
import collections
import glob
import html
import os
import re
import sys

EXPORT = sys.argv[1] if len(sys.argv) > 1 else "ChatExport_2026-08-05"

MSG_RE = re.compile(
    r'<div class="message (default clearfix(?: joined)?|service)" id="message(-?\d+)">'
    r'(.*?)(?=<div class="message (?:default clearfix(?: joined)?|service)" id="message'
    r'|\s*</div>\s*</div>\s*</div>\s*</body>)',
    re.S,
)
MEDIA_RE = re.compile(r'class="media clearfix pull_left(?: block_link)? (media_\w+)"')
DATE_RE = re.compile(r'title="(\d{2})\.(\d{2})\.(\d{4}) (\d{2}):')


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s)).strip()


def page_order(path):
    return int(re.search(r"messages(\d*)\.html", path).group(1) or 0)


def main():
    pages = sorted(glob.glob(os.path.join(EXPORT, "messages*.html")), key=page_order)
    if not pages:
        sys.exit(f"не найдено messages*.html в {EXPORT}")

    authors = collections.Counter()
    per_day = collections.Counter()
    per_hour = collections.Counter()
    media = collections.Counter()
    domains = collections.Counter()
    total = joined = service = replies = forwards = not_included = 0
    text_blocks = text_chars = 0
    voice_seconds = 0
    current_author = None

    for page in pages:
        with open(page, encoding="utf-8") as fh:
            src = fh.read()
        for m in MSG_RE.finditer(src):
            kind, _mid, body = m.group(1), m.group(2), m.group(3)
            if kind == "service":
                service += 1
                continue
            total += 1
            if "joined" in kind:
                joined += 1
            name = re.search(r'<div class="from_name">(.*?)</div>', body, re.S)
            if name:
                current_author = strip_tags(name.group(1))
            authors[current_author or "UNKNOWN"] += 1

            d = DATE_RE.search(body)
            if d:
                per_day[f"{d.group(3)}-{d.group(2)}-{d.group(1)}"] += 1
                per_hour[d.group(4)] += 1

            if "reply_to details" in body:
                replies += 1
            if "forwarded body" in body or "Forwarded from" in body:
                forwards += 1
            if "Not included" in body:
                not_included += 1
            for cls in MEDIA_RE.findall(body):
                media[cls] += 1

            txt = re.search(r'<div class="text">(.*?)</div>', body, re.S)
            if txt:
                t = strip_tags(txt.group(1))
                if t:
                    text_blocks += 1
                    text_chars += len(t)

            for url in re.findall(r'href="(https?://[^"]+)"', body):
                parts = url.split("/")
                if len(parts) > 2:
                    domains[parts[2]] += 1

            v = re.search(
                r'media_voice_message.*?<div class="status details">\s*(\d+):(\d+)', body, re.S
            )
            if v:
                voice_seconds += int(v.group(1)) * 60 + int(v.group(2))

    print(f"страниц: {len(pages)}")
    print(f"сообщений: {total} (joined: {joined}), служебных: {service}")
    print(f"авторов: {len(authors)}, из них с <=5 сообщений: "
          f"{sum(1 for c in authors.values() if c <= 5)}")
    print(f"ответов: {replies} ({replies / max(1, total):.1%}), пересылок: {forwards}")
    print(f"текстовых блоков: {text_blocks}, знаков: {text_chars} "
          f"(ср. {text_chars / max(1, text_blocks):.1f})")
    print(f"медиа: {dict(media)}")
    print(f"недоступных вложений (Not included): {not_included}")
    print(f"голосовые: суммарно {voice_seconds / 60:.1f} мин")
    print(f"период: {min(per_day)} .. {max(per_day)} ({len(per_day)} дней)")

    print("\nтоп-20 авторов:")
    for a, c in authors.most_common(20):
        print(f"  {c:6d}  {a}")
    print("\nпо дням:")
    for k in sorted(per_day):
        print(f"  {k} {per_day[k]}")
    print("\nпо часам:", " ".join(f"{h}:{per_hour[h]}" for h in sorted(per_hour)))
    print("\nтоп-15 доменов:")
    for d_, c in domains.most_common(15):
        print(f"  {c:5d}  {d_}")

    print("\nфайлы на диске:")
    for sub in ("photos", "voice_messages", "files", "stickers", "video_files"):
        p = os.path.join(EXPORT, sub)
        if os.path.isdir(p):
            names = os.listdir(p)
            originals = [n for n in names if "_thumb" not in n]
            print(f"  {sub:15s} всего {len(names):5d}, без превью {len(originals):5d}")


if __name__ == "__main__":
    main()
