#!/usr/bin/env python3
"""Уплотнённые срезы дней для сплошного чтения.

Сохраняет всё смысловое (длинные реплики, всё с реакциями, капс, вопросы,
медиа, пересылки, ночные реплики), а серии коротких проходных сообщений
схлопывает в одну строку со счётчиком и авторами — чтобы ритм и объём флуда
оставались видимыми, но не занимали место.

Выход: slices_condensed/YYYY-MM-DD.md
"""
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime

KIND_MARK = {
    "photo": "ФОТО", "sticker": "СТИКЕР", "voice": "ГОЛОСОВОЕ", "audio_file": "АУДИО",
    "video": "ВИДЕО", "animation": "ГИФ", "video_message": "КРУЖОК", "file": "ФАЙЛ",
}
NIGHT = set(range(0, 6))


def caps_ratio(t):
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isupper()) / len(letters)


def keep(r):
    t = r["text"]
    if r["media"] or r["is_forwarded"] or r["reactions"]:
        return True
    if len(t) >= 30:
        return True
    if len(t) >= 6 and caps_ratio(t) > 0.6:
        return True
    if "?" in t and len(t) >= 15:
        return True
    if r["hour"] in NIGHT and len(t) >= 20:
        return True
    return False


def fmt_media(r):
    out = []
    for m in r["media"]:
        mk = KIND_MARK.get(m["kind"], m["kind"].upper())
        if m.get("duration_sec") and m["kind"] in ("voice", "video", "animation", "video_message"):
            mk += f"{m['duration_sec']}с"
        if m["kind"] == "audio_file" and m.get("title"):
            mk += f"«{m['title']}»"
        out.append(f"[{mk}]")
    return "".join(out)


def main():
    rows = [json.loads(l) for l in open("corpus/messages.jsonl", encoding="utf-8")]
    alias_full = json.load(open("corpus/aliases.json", encoding="utf-8"))
    alias = {v: k for k, v in alias_full.items()}
    by_id = {r["id"]: r for r in rows}
    sessions = [json.loads(l) for l in open("corpus/sessions.jsonl", encoding="utf-8")]
    sess_bounds = {(s["first_id"], s["last_id"]): s for s in sessions}

    os.makedirs("slices_condensed", exist_ok=True)
    days = defaultdict(list)
    for r in rows:
        days[r["date"]].append(r)

    sess_by_start = {s["first_id"]: s for s in sessions}
    stats = []
    for date in sorted(days):
        day = days[date]
        wd = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][datetime.fromisoformat(date).weekday()]
        authors = Counter(r["author"] for r in day)
        night = sum(1 for r in day if r["hour"] in NIGHT)
        media = Counter(m["kind"] for r in day for m in r["media"])
        reactions = sum(max(1, len(x.get("users") or [])) for r in day for x in r["reactions"])
        lines = [
            f"# {date} ({wd}) · уплотнённый срез",
            f"сообщений: {len(day)} · авторов: {len(authors)} · ночных: {night} "
            f"({night*100//max(1,len(day))}%) · ответов: {sum(1 for r in day if r['reply_to'])} "
            f"· реакций: {reactions} · медиа: {dict(media) or '—'}",
            "актив: " + ", ".join(f"{alias_full.get(a,a)}({c})" for a, c in authors.most_common(10)),
            "«…N коротких реплик…» = схлопнутый флуд (короткие «да/ага/ахах», стикеры-ответы)",
            "",
        ]
        buf = []

        def flush():
            if not buf:
                return
            who = Counter(x["author"] for x in buf)
            sample = " / ".join(" ".join(x["text"].split())[:22] for x in buf[:4] if x["text"])
            lines.append(f"    …{len(buf)} коротких реплик: "
                         + ", ".join(f"{alias_full.get(a,a)}×{c}" for a, c in who.most_common(4))
                         + (f" — «{sample}»" if sample else ""))
            buf.clear()

        prev = None
        for r in day:
            if r["id"] in sess_by_start:
                flush()
                s = sess_by_start[r["id"]]
                lines.append(f"\n## {s['start'][11:16]}–{s['end'][11:16]} · {s['messages']} сообщ · "
                             f"{s['msgs_per_min']}/мин · "
                             + ", ".join(f"{alias_full.get(a,a)}({c})" for a, c in s["top_authors"][:5]))
            if keep(r):
                flush()
                head = f"{r['id']} {r['ts'][11:16]} {alias_full.get(r['author'], r['author'])}"
                if r["reply_to"] and (prev is None or r["reply_to"] != prev["id"]):
                    tgt = by_id.get(r["reply_to"])
                    head += f"→{alias_full.get(tgt['author'],'?')}#{r['reply_to']}" if tgt else f"→#{r['reply_to']}"
                elif r["reply_to"]:
                    head += "→^"
                if r["is_forwarded"]:
                    head += f"(фвд:{r['forwarded_from']})"
                react = "".join(f"({x['emoji']}{max(1, len(x.get('users') or []))})"
                                for x in r["reactions"])
                body = " ".join(r["text"].split())
                lines.append(f"{head}: {fmt_media(r)}{' ' if r['media'] and body else ''}{body}{react}".rstrip())
            else:
                buf.append(r)
            prev = r
        flush()
        text = "\n".join(lines)
        open(f"slices_condensed/{date}.md", "w", encoding="utf-8").write(text)
        stats.append((date, len(day), len(text)))

    total = sum(s[2] for s in stats)
    orig = sum(os.path.getsize(f"slices/{s[0]}.md") for s in stats)
    print(f"дней: {len(stats)} | символов: {total} (было {orig}, сжатие {100-total*100//orig}%)")
    for d, n, c in sorted(stats, key=lambda x: -x[2])[:6]:
        print(f"  {d}: {n} сообщ → {c} знаков")


if __name__ == "__main__":
    main()
