#!/usr/bin/env python3
"""Ф3: OCR фотографий (tesseract rus+eng) + перцептивный хэш для дедупликации.

Результат: media/photos.jsonl — по одной записи на фото, с привязкой к сообщению,
распознанным текстом, хэшем и грубой эвристической категорией.
Прогон возобновляемый: уже обработанные пути пропускаются.
"""
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

EXPORT = "ChatExport_2026-08-05"
OUT = "media/photos.jsonl"

try:
    from PIL import Image
    import imagehash
except ImportError:
    Image = imagehash = None

GAME_HINTS = re.compile(
    r"(win|bet|spin|balance|бонус|ставк|выигрыш|баланс|множител|free ?spins|x\d{2,}|"
    r"₽|руб|usd|\$\d|депозит|вывод|cash|jackpot|мультипликатор)", re.I)
CHAT_HINTS = re.compile(r"(написать сообщение|онлайн|печатает|подписчик|переслано|forwarded)", re.I)


def ocr(path):
    try:
        r = subprocess.run(
            ["tesseract", path, "stdout", "-l", "rus+eng", "--psm", "6"],
            capture_output=True, text=True, timeout=120)
        return re.sub(r"[ \t]+", " ", r.stdout).strip()
    except Exception as e:
        return f"__ERROR__ {e}"


def main():
    os.makedirs("media", exist_ok=True)
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                done.add(json.loads(line)["path"])
            except Exception:
                pass

    rows = [json.loads(l) for l in open("corpus/media_index.jsonl", encoding="utf-8")]
    msgs = {}
    for line in open("corpus/messages.jsonl", encoding="utf-8"):
        m = json.loads(line)
        msgs[m["id"]] = m

    targets, seen = [], set()
    for r in rows:
        if r["kind"] == "photo" and r["file_exists"] and r["path"] not in seen | done:
            seen.add(r["path"])
            targets.append(r)

    print(f"фото к обработке: {len(targets)} (уже готово: {len(done)})", flush=True)
    with open(OUT, "a", encoding="utf-8") as fh:
        for i, t in enumerate(targets, 1):
            full = os.path.join(EXPORT, t["path"])
            text = ocr(full)
            rec = {"path": t["path"], "msg_id": t["msg_id"], "ts": t["ts"],
                   "author": t["author"], "ocr_text": text[:4000],
                   "ocr_len": len(text)}
            msg = msgs.get(t["msg_id"], {})
            rec["caption"] = msg.get("text", "")[:300]
            rec["reactions"] = sum(max(1, len(x.get("users") or [])) for x in msg.get("reactions", []))
            if Image:
                try:
                    with Image.open(full) as im:
                        rec["size"] = list(im.size)
                        rec["phash"] = str(imagehash.phash(im))
                except Exception:
                    rec["size"], rec["phash"] = None, None
            low = text.lower()
            rec["auto_class"] = (
                "game_screen" if GAME_HINTS.search(low) and len(text) > 20 else
                "chat_screen" if CHAT_HINTS.search(low) else
                "text_heavy" if len(text) > 200 else
                "text_light" if len(text) > 20 else "no_text")
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if i % 50 == 0 or i == len(targets):
                print(f"  {i}/{len(targets)}", flush=True)

    # сводка по дублям
    groups = defaultdict(list)
    for line in open(OUT, encoding="utf-8"):
        r = json.loads(line)
        if r.get("phash"):
            groups[r["phash"]].append(r["path"])
    dups = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"групп дублей: {len(dups)}, файлов в дублях: {sum(len(v) for v in dups.values())}")


if __name__ == "__main__":
    main()
