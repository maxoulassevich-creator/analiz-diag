#!/usr/bin/env python3
"""Ф2: треды, сессии и суточные срезы для чтения человеком/моделью.

Выход:
  corpus/sessions.jsonl   — сессии (разрыв > 30 мин) с метриками
  corpus/threads.json     — деревья ответов, топ-треды
  slices/YYYY-MM-DD.md    — читаемый срез дня с сохранением структуры ответов
"""
import json
import os
from collections import Counter, defaultdict
from datetime import datetime

GAP_MIN = 30
NIGHT = set(range(0, 6))
KIND_MARK = {
    "photo": "ФОТО", "sticker": "СТИКЕР", "voice": "ГОЛОСОВОЕ",
    "audio_file": "АУДИО", "video": "ВИДЕО", "animation": "ГИФ",
    "video_message": "КРУЖОК", "file": "ФАЙЛ",
}


def load():
    return [json.loads(l) for l in open("corpus/messages.jsonl", encoding="utf-8")]


def dt(r):
    return datetime.fromisoformat(r["ts"])


def build_sessions(rows):
    sessions, cur = [], []
    for r in rows:
        if cur and (dt(r) - dt(cur[-1])).total_seconds() > GAP_MIN * 60:
            sessions.append(cur)
            cur = []
        cur.append(r)
    if cur:
        sessions.append(cur)
    out = []
    for i, s in enumerate(sessions, 1):
        span = (dt(s[-1]) - dt(s[0])).total_seconds() / 60
        authors = Counter(r["author"] for r in s)
        reactions = sum(max(1, len(x.get("users") or [])) for r in s for x in r["reactions"])
        media = Counter(m["kind"] for r in s for m in r["media"])
        out.append({
            "session": i, "start": s[0]["ts"], "end": s[-1]["ts"],
            "date": s[0]["date"], "minutes": round(span, 1), "messages": len(s),
            "authors": len(authors), "top_authors": authors.most_common(6),
            "msgs_per_min": round(len(s) / span, 2) if span > 0 else len(s),
            "night": s[0]["hour"] in NIGHT,
            "replies": sum(1 for r in s if r["reply_to"]),
            "reactions": reactions, "media": dict(media),
            "first_id": s[0]["id"], "last_id": s[-1]["id"],
            "chars": sum(r["text_len"] for r in s),
        })
    return sessions, out


def build_threads(rows):
    by_id = {r["id"]: r for r in rows}
    children = defaultdict(list)
    for r in rows:
        if r["reply_to"] and r["reply_to"] in by_id:
            children[r["reply_to"]].append(r["id"])
    roots = [r["id"] for r in rows if not r["reply_to"] or r["reply_to"] not in by_id]

    def collect(rid):
        stack, seen = [rid], []
        while stack:
            cur = stack.pop()
            seen.append(cur)
            stack.extend(children.get(cur, []))
        return seen

    threads = []
    for root in roots:
        ids = collect(root)
        if len(ids) < 3:
            continue
        ids.sort()
        members = [by_id[i] for i in ids]
        parts = Counter(m["author"] for m in members)
        span = (dt(members[-1]) - dt(members[0])).total_seconds() / 60
        threads.append({
            "root": root, "size": len(ids), "start": members[0]["ts"],
            "minutes": round(span, 1), "participants": parts.most_common(8),
            "reactions": sum(max(1, len(x.get("users") or [])) for m in members for x in m["reactions"]),
            "opener_author": members[0]["author"],
            "opener_text": members[0]["text"][:200],
            "ids": ids[:400],
        })
    threads.sort(key=lambda t: -t["size"])
    return threads


def build_aliases(rows):
    """Короткие псевдонимы для компактных срезов (полные имена — в corpus/aliases.json)."""
    alias, used = {}, set()
    for name, _ in Counter(r["author"] for r in rows).most_common():
        base = name.strip().split()[0] if name.strip() else "?"
        base = "".join(ch for ch in base if ch.isalnum() or ch in "_-.")[:12] or "?"
        cand, n = base, 1
        while cand in used:
            n += 1
            cand = f"{base}{n}"
        used.add(cand)
        alias[name] = cand
    return alias


def fmt_media(r):
    marks = []
    for m in r["media"]:
        mk = KIND_MARK.get(m["kind"], m["kind"].upper())
        if m["kind"] in ("voice", "video_message", "video", "animation") and m.get("duration_sec"):
            mk += f"{m['duration_sec']}с"
        if m["kind"] == "audio_file" and m.get("title"):
            mk += f"«{m['title']}»"
        if not m["available"]:
            mk += "!"
        marks.append(f"[{mk}]")
    return "".join(marks)


def fmt_msg(r, by_id, alias, prev):
    """Компактная строка: <id> <чч:мм> <автор>[→адресат]: [медиа] текст (реакции)"""
    parts = [str(r["id"]), r["ts"][11:16], alias.get(r["author"], r["author"])]
    head = f"{parts[0]} {parts[1]} {parts[2]}"
    # адресат указывается, только если это не ответ на предыдущее сообщение
    if r["reply_to"] and (prev is None or r["reply_to"] != prev["id"]):
        tgt = by_id.get(r["reply_to"])
        head += f"→{alias.get(tgt['author'], '?')}#{r['reply_to']}" if tgt else f"→#{r['reply_to']}"
    elif r["reply_to"]:
        head += "→^"
    if r["is_forwarded"]:
        head += f"(фвд:{r['forwarded_from']})"
    body = " ".join((r["text"] or "").split())
    react = ""
    if r["reactions"]:
        react = " " + "".join(
            f"({x['emoji']}{max(1, len(x.get('users') or []))})" for x in r["reactions"])
    return f"{head}: {fmt_media(r)}{' ' if r['media'] and body else ''}{body}{react}".rstrip()


def main():
    rows = load()
    by_id = {r["id"]: r for r in rows}
    alias = build_aliases(rows)
    sessions, session_meta = build_sessions(rows)
    threads = build_threads(rows)

    os.makedirs("corpus", exist_ok=True)
    json.dump({v: k for k, v in alias.items()}, open("corpus/aliases.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    os.makedirs("slices", exist_ok=True)
    with open("corpus/sessions.jsonl", "w", encoding="utf-8") as fh:
        for s in session_meta:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    with open("corpus/threads.json", "w", encoding="utf-8") as fh:
        json.dump({"total": len(threads), "top": threads[:300]}, fh, ensure_ascii=False, indent=1)

    sess_by_date = defaultdict(list)
    for s, meta in zip(sessions, session_meta):
        sess_by_date[meta["date"]].append((meta, s))

    index = []
    for date in sorted({r["date"] for r in rows}):
        day = [r for r in rows if r["date"] == date]
        wd = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][datetime.fromisoformat(date).weekday()]
        authors = Counter(r["author"] for r in day)
        night = sum(1 for r in day if r["hour"] in NIGHT)
        media = Counter(m["kind"] for r in day for m in r["media"])
        reactions = sum(max(1, len(x.get("users") or [])) for r in day for x in r["reactions"])
        lines = [
            f"# {date} ({wd})",
            f"сообщений: {len(day)} · авторов: {len(authors)} · ночных(00-05): {night} "
            f"({night*100//max(1,len(day))}%) · ответов: {sum(1 for r in day if r['reply_to'])} "
            f"· реакций: {reactions}",
            f"медиа: {dict(media) or '—'}",
            f"актив: " + ", ".join(f"{alias.get(a,a)}({c})" for a, c in authors.most_common(10)),
            "формат строки: <id> <чч:мм> <автор>[→адресат]: [медиа] текст (реакции); "
            "→^ = ответ на предыдущее сообщение, ! = файл не выгружен",
            "",
        ]
        for meta, s in sess_by_date[date]:
            lines.append(
                f"## сессия #{meta['session']} · {meta['start'][11:16]}–{meta['end'][11:16]} · "
                f"{meta['messages']} сообщ · {meta['msgs_per_min']}/мин · "
                + ", ".join(f"{alias.get(a,a)}({c})" for a, c in meta["top_authors"]))
            prev = None
            for r in s:
                lines.append(fmt_msg(r, by_id, alias, prev))
                prev = r
            lines.append("")
        text = "\n".join(lines)
        open(f"slices/{date}.md", "w", encoding="utf-8").write(text)
        index.append({"date": date, "messages": len(day), "chars": len(text),
                      "night_share": round(night / max(1, len(day)), 2),
                      "authors": len(authors), "reactions": reactions})

    json.dump(index, open("slices/index.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"сессий: {len(session_meta)} | тредов (>=3 сообщ): {len(threads)} | "
          f"дней: {len(index)} | символов в срезах: {sum(i['chars'] for i in index)}")
    print("топ-5 тредов:", [(t["size"], t["opener_text"][:40]) for t in threads[:5]])


if __name__ == "__main__":
    main()
