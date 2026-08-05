#!/usr/bin/env python3
"""Ф4 (машинная часть): объективные сигналы, на которые опирается ручная аннотация.

Выход:
  reports/metrics.json / metrics.md — метрики по дням, авторам, лексике
  reports/signals.md — автоматически отобранные высокосигнальные сообщения:
      хиты по реакциям, ночные исповеди, капс, длинные тексты, горячие треды
"""
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime

NIGHT = set(range(0, 6))
WORD = re.compile(r"[А-Яа-яЁёA-Za-z]{3,}")

PAIN = re.compile(
    r"(груст|тоск|одинок|устал|больно|плак|слёз|слез|депресс|сдох|умер|смерт|похорон|"
    r"жаль|прост(и|ите)|виноват|потер|бросил|ушла|ушёл|расста|развод|долг|занял|"
    r"кредит|проигр|слил|проспал|бессонниц|не сплю|пусто|нахуй всё|надоел|зачем жить|"
    r"страшно|боюсь|болею|больниц|скорая|операц)", re.I)
RAGE = re.compile(
    r"(бля|хуй|пизд|ебан|ёбан|ебал|сука|мраз|тварь|уёб|уеб|гандон|долбоёб|долбоеб|"
    r"idiot|дебил|завали|заткни|нахуй|пошёл ты|пошел ты|срач|разъеб|разьеб|"
    r"убью|ненавиж|бесит|заебал|охуел|ахуел)", re.I)
JOY = re.compile(
    r"(ахах|хаха|ржу|ору|угар|смешно|лол|kekw|)|(занос|выигр|подня|фарт|"
    r"красав|красота|топ|огонь|кайф|обожаю|люблю|спасибо|поздрав)", re.I)
GAMBLE = re.compile(
    r"(слот|занос|казино|ставк|депоз|прагмат|нолимит|nolimit|pragmat|бонус|фриспин|"
    r"иксов|x\d{2,}|максвин|max ?win|вывел|проиграл|баланс|1win|кэф|экспресс|беттинг)", re.I)


def load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")]


def main():
    rows = load("corpus/messages.jsonl")
    os.makedirs("reports", exist_ok=True)
    by_id = {r["id"]: r for r in rows}
    alias = json.load(open("corpus/aliases.json", encoding="utf-8"))
    full2short = {v: k for k, v in alias.items()}

    # --- метрики по дням -------------------------------------------------
    days = defaultdict(list)
    for r in rows:
        days[r["date"]].append(r)

    day_rows = []
    for date in sorted(days):
        d = days[date]
        txt = " ".join(r["text"] for r in d)
        reactions = Counter()
        for r in d:
            for x in r["reactions"]:
                reactions[x["emoji"]] += max(1, len(x.get("users") or []))
        # плотность: максимум сообщений за 10-минутное окно
        stamps = sorted(datetime.fromisoformat(r["ts"]) for r in d)
        peak, j = 0, 0
        for i, t in enumerate(stamps):
            while (t - stamps[j]).total_seconds() > 600:
                j += 1
            peak = max(peak, i - j + 1)
        day_rows.append({
            "date": date,
            "weekday": ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][datetime.fromisoformat(date).weekday()],
            "messages": len(d),
            "authors": len({r["author"] for r in d}),
            "chars": sum(r["text_len"] for r in d),
            "night_msgs": sum(1 for r in d if r["hour"] in NIGHT),
            "replies": sum(1 for r in d if r["reply_to"]),
            "media": sum(len(r["media"]) for r in d),
            "voice": sum(1 for r in d for m in r["media"] if m["kind"] == "voice"),
            "photos": sum(1 for r in d for m in r["media"] if m["kind"] == "photo"),
            "stickers": sum(1 for r in d for m in r["media"] if m["kind"] == "sticker"),
            "reactions": sum(reactions.values()),
            "top_reactions": reactions.most_common(5),
            "peak_10min": peak,
            "pain_hits": len(PAIN.findall(txt)),
            "rage_hits": len(RAGE.findall(txt)),
            "joy_hits": len(JOY.findall(txt)),
            "gamble_hits": len(GAMBLE.findall(txt)),
            "top_authors": Counter(r["author"] for r in d).most_common(8),
        })

    # --- профили авторов -------------------------------------------------
    authors = defaultdict(list)
    for r in rows:
        authors[r["author"]].append(r)
    given = Counter()
    for r in rows:
        for x in r["reactions"]:
            for u in x.get("users") or []:
                given[u] += 1

    author_rows = []
    for name, msgs in sorted(authors.items(), key=lambda kv: -len(kv[1])):
        if len(msgs) < 20:
            continue
        txt = " ".join(m["text"] for m in msgs)
        words = Counter(w.lower() for m in msgs for w in WORD.findall(m["text"]))
        got = sum(max(1, len(x.get("users") or [])) for m in msgs for x in m["reactions"])
        author_rows.append({
            "author": name, "alias": alias.get(name, name), "messages": len(msgs),
            "chars": sum(m["text_len"] for m in msgs),
            "avg_len": round(sum(m["text_len"] for m in msgs) / len(msgs), 1),
            "night_share": round(sum(1 for m in msgs if m["hour"] in NIGHT) / len(msgs), 2),
            "reply_share": round(sum(1 for m in msgs if m["reply_to"]) / len(msgs), 2),
            "reactions_got": got, "reactions_given": given.get(name, 0),
            "media": sum(len(m["media"]) for m in msgs),
            "voice": sum(1 for m in msgs for x in m["media"] if x["kind"] == "voice"),
            "first_seen": msgs[0]["ts"][:10], "last_seen": msgs[-1]["ts"][:10],
            "active_days": len({m["date"] for m in msgs}),
            "pain": len(PAIN.findall(txt)), "rage": len(RAGE.findall(txt)),
            "gamble": len(GAMBLE.findall(txt)),
            "top_words": [w for w, _ in words.most_common(400)
                          if len(w) > 4][:15],
        })

    # --- лексика: частоты и первое употребление ---------------------------
    freq, first_use = Counter(), {}
    for r in rows:
        for w in WORD.findall(r["text"]):
            w = w.lower()
            freq[w] += 1
            if w not in first_use:
                first_use[w] = {"id": r["id"], "ts": r["ts"], "author": r["author"],
                                "text": r["text"][:160]}
    # фразы-мемы: повторяющиеся 2-3-словные сочетания
    phrases = Counter()
    for r in rows:
        ws = [w.lower() for w in WORD.findall(r["text"])]
        for n in (2, 3):
            for i in range(len(ws) - n + 1):
                phrases[" ".join(ws[i:i + n])] += 1

    # --- высокосигнальные сообщения ---------------------------------------
    def score_react(r):
        return sum(max(1, len(x.get("users") or [])) for x in r["reactions"])

    hits = sorted([r for r in rows if r["reactions"]], key=score_react, reverse=True)[:120]
    night_conf = sorted([r for r in rows if r["hour"] in NIGHT and r["text_len"] > 250],
                        key=lambda r: -r["text_len"])[:120]
    longest = sorted(rows, key=lambda r: -r["text_len"])[:80]
    caps = [r for r in rows if r["text_len"] > 25 and
            sum(1 for c in r["text"] if c.isupper()) / max(1, sum(1 for c in r["text"] if c.isalpha())) > 0.7][:80]
    pain_msgs = sorted([r for r in rows if r["text_len"] > 80 and len(PAIN.findall(r["text"])) >= 2],
                       key=lambda r: -len(PAIN.findall(r["text"])))[:120]
    gamble_msgs = sorted([r for r in rows if len(GAMBLE.findall(r["text"])) >= 2],
                         key=lambda r: -len(GAMBLE.findall(r["text"])))[:120]

    out = {
        "days": day_rows, "authors": author_rows,
        "top_words": freq.most_common(400),
        "top_phrases": [(p, c) for p, c in phrases.most_common(400) if c >= 8][:250],
    }
    json.dump(out, open("reports/metrics.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump({w: first_use[w] for w, c in freq.most_common(1200)},
              open("reports/first_use.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    def block(title, msgs, limit=None):
        lines = [f"\n## {title}\n"]
        for r in (msgs[:limit] if limit else msgs):
            react = "".join(f"({x['emoji']}{max(1, len(x.get('users') or []))})" for x in r["reactions"])
            body = " ".join(r["text"].split())[:400]
            lines.append(f"- `#{r['id']}` {r['ts'][:16]} **{r['author']}**{react}: {body}")
        return "\n".join(lines)

    md = ["# Автоматически отобранные высокосигнальные сообщения",
          "Отбор машинный (реакции, длина, время суток, лексические маркеры). "
          "Служит подсказкой для ручного чтения, а не заменой его."]
    md.append(block("Хиты по реакциям (топ-120)", hits))
    md.append(block("Ночные длинные сообщения 00:00–06:00 (топ-120)", night_conf))
    md.append(block("Самые длинные сообщения (топ-80)", longest))
    md.append(block("КАПСОМ (топ-80)", caps))
    md.append(block("Маркеры боли (топ-120)", pain_msgs))
    md.append(block("Маркеры гэмблинга (топ-120)", gamble_msgs))
    open("reports/signals.md", "w", encoding="utf-8").write("\n".join(md))

    lines = ["# Метрики\n", "## По дням\n",
             "| дата | дн | сообщ | авт | зн | ночь | отв | медиа | гол | фото | стик | реакц | пик/10мин | боль | ярость | гэмбл |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for d in day_rows:
        lines.append("| {date} | {weekday} | {messages} | {authors} | {chars} | {night_msgs} | "
                     "{replies} | {media} | {voice} | {photos} | {stickers} | {reactions} | "
                     "{peak_10min} | {pain_hits} | {rage_hits} | {gamble_hits} |".format(**d))
    lines += ["\n## Авторы (>=20 сообщений)\n",
              "| автор | сообщ | ср.длина | ночь | ответы | реакц получ | реакц дал | медиа | гол | дней | первый | последний |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for a in author_rows:
        lines.append("| {author} | {messages} | {avg_len} | {night_share} | {reply_share} | "
                     "{reactions_got} | {reactions_given} | {media} | {voice} | {active_days} | "
                     "{first_seen} | {last_seen} |".format(**a))
    open("reports/metrics.md", "w", encoding="utf-8").write("\n".join(lines))
    print(f"дней: {len(day_rows)} | авторов в профилях: {len(author_rows)} | "
          f"фраз-кандидатов: {len(out['top_phrases'])}")


if __name__ == "__main__":
    main()
