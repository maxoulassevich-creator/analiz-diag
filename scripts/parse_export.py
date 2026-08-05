#!/usr/bin/env python3
"""Ф1: парсинг HTML-выгрузки Telegram в нормализованный корпус.

Вход:  ChatExport_*/messages*.html
Выход: corpus/messages.jsonl, corpus/service.jsonl, corpus/media_index.jsonl,
       corpus/authors.json, corpus/stats.json

Особенности выгрузки, которые здесь учитываются:
  * сообщения "joined" не содержат блока автора — автор наследуется;
  * пересылки хранят вложенный from_name/text внутри div.forwarded body;
  * реакции присутствуют (span.reactions) вместе с именами реагировавших;
  * кастомные эмодзи оформлены как <a href="stickers/...">эмодзи</a>;
  * часть медиа не выгружена ("Not included") — фиксируется available=false.
"""
import html
import json
import os
import re
import sys
from collections import Counter, defaultdict
from html.parser import HTMLParser

EXPORT = sys.argv[1] if len(sys.argv) > 1 else "ChatExport_2026-08-05"
OUT = sys.argv[2] if len(sys.argv) > 2 else "corpus"

VOID = {"br", "img", "meta", "link", "input", "hr", "source"}
DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4}) (\d{2}):(\d{2}):(\d{2})")
GOTO_RE = re.compile(r"go_to_message(\d+)")
DUR_RE = re.compile(r"^(\d+):(\d{2})(?::(\d{2}))?")
SIZE_RE = re.compile(r"([\d.]+)\s*(B|KB|MB|GB)", re.I)
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_status(status):
    """'00:26, 2.8 MB' -> (26 сек, 2936012 байт)"""
    dur = size = None
    m = DUR_RE.match(status.strip())
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        dur = int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)
    m = SIZE_RE.search(status)
    if m:
        mult = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3}[m.group(2).lower()]
        size = int(float(m.group(1)) * mult)
    return dur, size


class ExportParser(HTMLParser):
    """Собирает сообщения со страницы, отслеживая вложенность блоков по классам."""

    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.page = page
        self.messages = []
        self.cur = None
        self.stack = []  # (tag, label)

    # --- служебное -----------------------------------------------------
    def ctx(self):
        return [lbl for _, lbl in self.stack if lbl]

    def in_fwd(self):
        return "fwd" in self.ctx()

    def flush(self):
        if self.cur is not None:
            self.messages.append(self.cur)
            self.cur = None

    # --- обработчики ---------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").strip()
        label = None

        if tag == "div" and cls.startswith("message "):
            self.flush()
            self.stack = []
            mid = a.get("id", "")
            self.cur = {
                "id": int(mid.replace("message", "")) if mid[7:].lstrip("-").isdigit() else None,
                "page": self.page,
                "kind": "service" if "service" in cls else "message",
                "joined": "joined" in cls,
                "ts": None, "author": None, "reply_to": None,
                "forwarded_from": None, "forwarded_ts": None,
                "_text": [], "_fwd_text": [], "_from": [], "_fwd_from": [],
                "media": [], "links": [], "custom_emoji": [], "reactions": [],
                "_svc": [],
            }
            label = "message"
        elif self.cur is not None:
            if "forwarded" in cls and "body" in cls:
                label = "fwd"
            elif cls == "from_name":
                label = "fwd_from" if self.in_fwd() else "from_name"
            elif cls.startswith("reply_to"):
                label = "reply"
            elif cls == "text":
                label = "fwd_text" if self.in_fwd() else "text"
            elif cls == "media_wrap clearfix":
                label = "media"
            elif cls == "reactions":
                label = "reactions"
            elif cls == "reaction":
                label = "reaction"
                self.cur["reactions"].append({"emoji": "", "users": [], "count": 0})
            elif cls == "emoji":
                label = "emoji"
            elif cls == "count":
                label = "count"
            elif cls == "body details" and self.cur["kind"] == "service":
                label = "service_text"
            elif cls.startswith("title bold"):
                label = "media_title"
            elif cls == "description":
                label = "media_desc"
            elif cls.startswith("status details"):
                label = "media_status"

            # имена реагировавших лежат в title у div.initials внутри span.reaction
            if cls.startswith("initials") and a.get("title") and "reaction" in self.ctx():
                if self.cur["reactions"]:
                    self.cur["reactions"][-1]["users"].append(a["title"].strip())

            # дата сообщения (не дата пересылки — та лежит внутри from_name)
            if "date" in cls and "details" in cls and a.get("title"):
                m = DATE_RE.search(a["title"])
                if m:
                    ts = "{}-{}-{}T{}:{}:{}+03:00".format(
                        m.group(3), m.group(2), m.group(1), m.group(4), m.group(5), m.group(6))
                    if self.in_fwd():
                        self.cur["forwarded_ts"] = self.cur["forwarded_ts"] or ts
                    elif self.cur["ts"] is None:
                        self.cur["ts"] = ts

            href = a.get("href", "")
            if tag == "a" and href:
                if href.startswith("http"):
                    self.cur["links"].append(href)
                elif href.startswith(("stickers/", "video_files/")):
                    self.cur["custom_emoji"].append(href)
                elif href.startswith(("photos/", "files/", "voice_messages/")):
                    kind = ("photo" if href.startswith("photos/") else
                            "voice" if href.startswith("voice_messages/") else "file")
                    if "media_voice_message" in cls:
                        kind = "voice"
                    elif "media_audio_file" in cls:
                        kind = "audio_file"
                    elif "photo_wrap" in cls:
                        kind = "photo"
                    self.cur["media"].append({
                        "kind": kind, "path": html.unescape(href), "available": True,
                        "title": None, "duration_sec": None, "size_bytes": None,
                        "in_forward": self.in_fwd(),
                    })
            # медиа без файла ("Not included")
            if tag == "div" and cls.startswith("media clearfix") and "block_link" not in cls:
                mk = "other"
                for k in ("media_video", "media_photo", "media_audio_file",
                          "media_voice_message", "media_file"):
                    if k in cls:
                        mk = k.replace("media_", "")
                        break
                self.cur["media"].append({
                    "kind": mk, "path": None, "available": False, "title": None,
                    "duration_sec": None, "size_bytes": None, "in_forward": self.in_fwd(),
                })

        if tag not in VOID:
            self.stack.append((tag, label))
        elif tag == "br" and self.cur is not None:
            key = "_fwd_text" if self.in_fwd() else "_text"
            if "text" in " ".join(self.ctx()):
                self.cur[key].append("\n")

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.cur is None or not data.strip():
            return
        ctx = self.ctx()
        top = ctx[-1] if ctx else None
        txt = data
        if top == "text":
            self.cur["_text"].append(txt)
        elif top == "fwd_text":
            self.cur["_fwd_text"].append(txt)
        elif top == "from_name":
            self.cur["_from"].append(txt)
        elif top == "fwd_from":
            self.cur["_fwd_from"].append(txt)
        elif top == "service_text":
            self.cur["_svc"].append(txt)
        elif top == "emoji" and self.cur["reactions"]:
            self.cur["reactions"][-1]["emoji"] += txt.strip()
        elif top == "count" and self.cur["reactions"]:
            try:
                self.cur["reactions"][-1]["count"] = int(txt.strip())
            except ValueError:
                pass
        elif top in ("media_title", "media_desc", "media_status") and self.cur["media"]:
            m = self.cur["media"][-1]
            val = txt.strip()
            if top == "media_title":
                m["title"] = val
            elif top == "media_desc" and "Not included" in val:
                m["available"] = False
            elif top == "media_status":
                d, s = parse_status(val)
                m["duration_sec"], m["size_bytes"] = d, s
        elif top == "reply":
            pass

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def close_page(self):
        self.flush()
        return self.messages


def collect_reply_ids(block_html):
    m = GOTO_RE.search(block_html)
    return int(m.group(1)) if m else None


def main():
    pages = sorted(
        [p for p in os.listdir(EXPORT) if re.fullmatch(r"messages\d*\.html", p)],
        key=lambda p: int(re.search(r"messages(\d*)\.html", p).group(1) or 0),
    )
    os.makedirs(OUT, exist_ok=True)

    all_msgs, service = [], []
    authors, alias_seen = {}, defaultdict(Counter)
    prev_author = None

    for page in pages:
        raw = open(os.path.join(EXPORT, page), encoding="utf-8").read()
        # reply_to берём регуляркой по блокам — надёжнее, чем через контекст парсера
        blocks = re.split(r'(?=<div class="message (?:default clearfix(?: joined)?|service)" id=")', raw)
        replies = {}
        for b in blocks:
            mid = re.search(r'id="message(-?\d+)"', b)
            if not mid:
                continue
            rt = re.search(r'<div class="reply_to details">.*?go_to_message(\d+)', b, re.S)
            if rt:
                replies[int(mid.group(1))] = int(rt.group(1))

        p = ExportParser(page)
        p.feed(raw)
        for m in p.close_page():
            if m["kind"] == "service":
                service.append({"id": m["id"], "page": page,
                                "text": " ".join(m["_svc"]).strip()})
                continue
            author = " ".join(m["_from"]).strip() or None
            inherited = author is None
            if author:
                prev_author = author
            author = author or prev_author or "UNKNOWN"
            if author not in authors:
                authors[author] = "a%03d" % (len(authors) + 1)
            alias_seen[author.strip().lower()][author] += 1

            text = "".join(m["_text"]).strip()
            fwd_text = "".join(m["_fwd_text"]).strip()
            fwd_from = " ".join(m["_fwd_from"]).strip() or None
            body = text or fwd_text
            ts = m["ts"] or ""
            date, hour = (ts[:10], int(ts[11:13])) if len(ts) > 13 else ("", -1)
            rec = {
                "id": m["id"], "page": page, "ts": ts, "date": date, "hour": hour,
                "author": author, "author_id": authors[author],
                "author_inherited": inherited,
                "reply_to": replies.get(m["id"]),
                "forwarded_from": fwd_from, "forwarded_ts": m["forwarded_ts"],
                "text": body, "text_len": len(body),
                "is_forwarded": bool(fwd_from),
                "media": m["media"], "has_media": bool(m["media"]),
                "links": m["links"],
                "link_domains": sorted({l.split("/")[2] for l in m["links"] if "//" in l}),
                "custom_emoji": m["custom_emoji"],
                "reactions": [r for r in m["reactions"] if r["emoji"]],
            }
            all_msgs.append(rec)

    all_msgs.sort(key=lambda r: (r["ts"], r["id"] or 0))

    # индекс медиа + проверка наличия файлов на диске
    media_index = []
    for r in all_msgs:
        for i, md in enumerate(r["media"]):
            # стикеры экспортируются как photo-блок с заголовком Sticker и без файла
            if md["kind"] == "photo" and (md["title"] or "").lower().startswith("sticker"):
                md["kind"] = "sticker"
            elif md["kind"] == "video" and md["title"]:
                t = md["title"].lower()
                md["kind"] = ("animation" if "animation" in t else
                              "video_message" if "video message" in t else "video")
            exists = bool(md["path"]) and os.path.exists(os.path.join(EXPORT, md["path"]))
            if md["path"] and not exists:
                md["available"] = False
            media_index.append({
                "msg_id": r["id"], "ts": r["ts"], "author": r["author"], "idx": i,
                "kind": md["kind"], "path": md["path"], "file_exists": exists,
                "available": md["available"], "title": md["title"],
                "duration_sec": md["duration_sec"], "size_bytes": md["size_bytes"],
                "in_forward": md["in_forward"],
            })

    def dump(name, rows):
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    dump("messages.jsonl", all_msgs)
    dump("service.jsonl", service)
    dump("media_index.jsonl", media_index)

    per_day, per_hour, per_author = Counter(), Counter(), Counter()
    kinds, reactions_by_emoji, reactors = Counter(), Counter(), Counter()
    for r in all_msgs:
        per_day[r["date"]] += 1
        per_hour[r["hour"]] += 1
        per_author[r["author"]] += 1
        for md in r["media"]:
            kinds[md["kind"]] += 1
        for rc in r["reactions"]:
            reactions_by_emoji[rc["emoji"]] += max(1, len(rc["users"]) or rc["count"] or 1)

    stats = {
        "pages": len(pages),
        "messages": len(all_msgs),
        "service": len(service),
        "inherited_author": sum(1 for r in all_msgs if r["author_inherited"]),
        "unknown_author": sum(1 for r in all_msgs if r["author"] == "UNKNOWN"),
        "with_text": sum(1 for r in all_msgs if r["text"]),
        "text_chars": sum(r["text_len"] for r in all_msgs),
        "replies": sum(1 for r in all_msgs if r["reply_to"]),
        "forwarded": sum(1 for r in all_msgs if r["is_forwarded"]),
        "with_media": sum(1 for r in all_msgs if r["has_media"]),
        "media_objects": len(media_index),
        "media_available": sum(1 for m in media_index if m["file_exists"]),
        "media_missing": sum(1 for m in media_index if not m["file_exists"]),
        "media_by_kind": dict(kinds),
        "reactions_total": sum(reactions_by_emoji.values()),
        "reaction_emoji_distinct": len(reactions_by_emoji),
        "authors": len(per_author),
        "days": len(per_day),
        "date_min": min(per_day) if per_day else None,
        "date_max": max(per_day) if per_day else None,
        "per_day": dict(sorted(per_day.items())),
        "per_hour": {str(h): per_hour[h] for h in sorted(per_hour)},
        "top_authors": per_author.most_common(40),
        "top_reactions": reactions_by_emoji.most_common(30),
    }
    with open(os.path.join(OUT, "stats.json"), "w", encoding="utf-8") as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "authors.json"), "w", encoding="utf-8") as fh:
        json.dump({"ids": authors, "counts": per_author.most_common()}, fh,
                  ensure_ascii=False, indent=1)

    print(json.dumps({k: v for k, v in stats.items()
                      if k not in ("per_day", "per_hour", "top_authors", "top_reactions")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
