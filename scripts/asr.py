#!/usr/bin/env python3
"""Ф3: расшифровка аудио (голосовые сообщения и треки) через faster-whisper.

Пишет результат построчно в JSONL, поэтому прогон можно прерывать и
продолжать: уже расшифрованные файлы пропускаются.

Запуск:
  python3 scripts/asr.py voice   media/voice_transcripts.jsonl [model]
  python3 scripts/asr.py tracks  media/track_transcripts.jsonl [model]
"""
import json
import os
import sys
import time

EXPORT = "ChatExport_2026-08-05"
MODE = sys.argv[1] if len(sys.argv) > 1 else "voice"
OUTPATH = sys.argv[2] if len(sys.argv) > 2 else f"media/{MODE}_transcripts.jsonl"
MODEL = sys.argv[3] if len(sys.argv) > 3 else "small"


def load_targets():
    """Список файлов на расшифровку с привязкой к сообщениям."""
    rows = [json.loads(l) for l in open("corpus/media_index.jsonl", encoding="utf-8")]
    if MODE == "voice":
        return [r for r in rows if r["kind"] == "voice" and r["file_exists"]]
    own = json.load(open("media/own_tracks.json", encoding="utf-8"))
    paths = set(own["paths"])
    seen, out = set(), []
    for r in rows:
        if r["kind"] == "audio_file" and r["file_exists"] and r["path"] in paths:
            if r["path"] in seen:
                continue
            seen.add(r["path"])
            out.append(r)
    return out


def main():
    os.makedirs(os.path.dirname(OUTPATH) or ".", exist_ok=True)
    done = set()
    if os.path.exists(OUTPATH):
        for line in open(OUTPATH, encoding="utf-8"):
            try:
                done.add(json.loads(line)["path"])
            except Exception:
                pass

    targets = [t for t in load_targets() if t["path"] not in done]
    print(f"режим={MODE} модель={MODEL} к расшифровке={len(targets)} уже готово={len(done)}",
          flush=True)
    if not targets:
        return

    from faster_whisper import WhisperModel
    model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=4)

    t0 = time.time()
    with open(OUTPATH, "a", encoding="utf-8") as fh:
        for i, t in enumerate(targets, 1):
            path = os.path.join(EXPORT, t["path"])
            try:
                segments, info = model.transcribe(
                    path, language="ru", vad_filter=True, beam_size=5,
                    condition_on_previous_text=False,
                )
                segs = [{"start": round(s.start, 1), "end": round(s.end, 1),
                         "text": s.text.strip(),
                         "no_speech_prob": round(getattr(s, "no_speech_prob", 0.0), 3)}
                        for s in segments]
                rec = {
                    "path": t["path"], "msg_id": t["msg_id"], "ts": t["ts"],
                    "author": t["author"], "kind": t["kind"], "title": t.get("title"),
                    "duration_sec": t.get("duration_sec") or round(info.duration, 1),
                    "text": " ".join(s["text"] for s in segs).strip(),
                    "segments": segs,
                }
            except Exception as e:  # битый файл не должен ронять прогон
                rec = {"path": t["path"], "msg_id": t["msg_id"], "ts": t["ts"],
                       "author": t["author"], "kind": t["kind"], "error": str(e),
                       "text": ""}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            if i % 10 == 0 or i == len(targets):
                el = time.time() - t0
                print(f"  {i}/{len(targets)} за {el/60:.1f} мин "
                      f"(~{el/i:.1f} с/файл)", flush=True)


if __name__ == "__main__":
    main()
