"""Excerto do guidance: vai ao comunicado de resultados na SEC e tira as frases de previsão.

Só para empresas que entregam na SEC (as cotadas nos EUA). Sem IA: procura frases com palavras
como "outlook", "guidance" ou "expects" que tenham números. O texto vem em inglês, como no comunicado.

Uso:  python guidance.py SIMBOLO
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
TICKERS_CACHE = DATA / "sec_tickers.json"

KEYWORDS = re.compile(r"\b(outlook|guidance|expects?|expected|anticipates?|forecasts?|projects?|targets?|raises?|raised|"
                      r"lowers?|lowered|reaffirm\w*|reiterat\w*)\b", re.I)
FUTURE = re.compile(r"\b(full[- ]year|fiscal(?: year)? ?20\d\d|fy ?20?\d\d|next quarter|(first|second|third|fourth) quarter"
                    r"(?: of)?(?: fiscal)? ?20\d\d|q[1-4]|20\d\d|outlook|guidance)\b", re.I)
NUMBER = re.compile(r"(\$\s?\d|\d+(\.\d+)?\s?(%|percent|million|billion|bps|basis points))", re.I)
BOILERPLATE = re.compile(r"forward[- ]looking|safe harbor|undue reliance|risks? and uncertaint|actual results|"
                         r"no obligation|private securities litigation|non-gaap financial measures? (is|are|should)|"
                         r"conference call|webcast|reconciliation", re.I)


def _get(url: str, user_agent: str) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}, timeout=30)
    r.raise_for_status()
    time.sleep(0.2)  # a SEC pede no máximo 10 pedidos por segundo
    return r


def cik_for(symbol: str, user_agent: str) -> int | None:
    today = date.today().isoformat()
    table = None
    if TICKERS_CACHE.exists():
        c = json.loads(TICKERS_CACHE.read_text(encoding="utf-8"))
        if c.get("dia") == today:
            table = c["cik"]
    if table is None:
        raw = _get("https://www.sec.gov/files/company_tickers.json", user_agent).json()
        table = {v["ticker"].upper(): v["cik_str"] for v in raw.values()}
        DATA.mkdir(exist_ok=True)
        TICKERS_CACHE.write_text(json.dumps({"dia": today, "cik": table}), encoding="utf-8")
    return table.get(symbol.upper()) or table.get(symbol.upper().replace("-", "."))


def latest_release(cik: int, user_agent: str, max_age_days: int = 4) -> dict | None:
    """O comunicado de resultados mais recente: 8-K com o ponto 2.02, ou 6-K para empresas estrangeiras."""
    sub = _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", user_agent).json()
    rec = sub["filings"]["recent"]
    cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
    best = None
    for form, items, acc, filed, primary in zip(rec["form"], rec["items"], rec["accessionNumber"],
                                                rec["filingDate"], rec["primaryDocument"]):
        if filed < cutoff:
            break
        if (form == "8-K" and "2.02" in items) or (form == "6-K" and best is None):
            best = {"form": form, "accession": acc, "filed": filed, "primary": primary, "cik": cik}
            if form == "8-K":
                break
    return best


def release_document(filing: dict, user_agent: str) -> tuple[str, str]:
    """Devolve (url, html) do anexo com o comunicado (normalmente o 99.1)."""
    base = f"https://www.sec.gov/Archives/edgar/data/{filing['cik']}/{filing['accession'].replace('-', '')}/"
    items = _get(base + "index.json", user_agent).json()["directory"]["item"]
    docs = [i for i in items if i["name"].lower().endswith((".htm", ".html")) and "index" not in i["name"].lower()]

    def score(i: dict) -> tuple:
        n = i["name"].lower()
        is_ex99 = bool(re.search(r"ex[-_]?99|99[-_.]?1|press|earnings|release", n))
        return (is_ex99, n != filing["primary"].lower(), int(i.get("size") or 0))

    if not docs:
        raise ValueError("comunicado sem documentos html")
    doc = max(docs, key=score)
    return base + doc["name"], _get(base + doc["name"], user_agent).text


def to_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", page)
    page = re.sub(r"(?i)</(p|div|tr|li|h\d|table)>|<br\s*/?>", "\n", page)
    page = html.unescape(re.sub(r"<[^>]+>", " ", page)).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", page)


def extract(text: str, limit: int = 3, max_chars: int = 260) -> list[str]:
    """Frases de previsão: palavra de previsão + período futuro + um número, sem texto jurídico."""
    found, seen = [], set()
    for block in text.split("\n"):
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", block.strip()):
            s = s.strip(" •·-–\t")
            if not (40 <= len(s) <= 600) or BOILERPLATE.search(s):
                continue
            if not (KEYWORDS.search(s) and NUMBER.search(s) and FUTURE.search(s)):
                continue
            key = re.sub(r"\W+", "", s.lower())[:60]
            if key in seen:
                continue
            seen.add(key)
            strong = bool(re.search(r"\b(outlook|guidance)\b", s, re.I)) + bool(re.search(r"\bexpect", s, re.I))
            found.append((-strong, len(found), s))
    best = sorted(found)[:limit]
    return [s if len(s) <= max_chars else s[:max_chars].rsplit(" ", 1)[0] + "…" for _, _, s in sorted(best, key=lambda x: x[1])]


def guidance_for(symbol: str, user_agent: str) -> dict | None:
    """{'url': ..., 'frases': [...], 'data': ...} ou None se não houver comunicado recente."""
    cik = cik_for(symbol, user_agent)
    if not cik:
        return None
    filing = latest_release(cik, user_agent)
    if not filing:
        return None
    url, page = release_document(filing, user_agent)
    return {"url": url, "frases": extract(to_text(page)), "data": filing["filed"], "tipo": filing["form"]}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    out = guidance_for(sys.argv[1], cfg["sec_user_agent"])
    print(json.dumps(out, ensure_ascii=False, indent=1))
