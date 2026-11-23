"""Comunicados de resultados na SEC: deteção rápida, EPS e excerto do guidance.

Só para empresas que entregam na SEC (as cotadas nos EUA). Sem IA:
- recent_results() lê o feed de entregas recentes da SEC (8-K com o ponto 2.02 e 6-K), que mostra
  um comunicado minutos depois de sair, horas antes de o Yahoo ter o EPS;
- eps_from_release() lê o EPS do trimestre no comunicado (ajustado, se houver) e não devolve nada
  quando não há um valor claro;
- extract() tira as frases com "outlook", "guidance" ou "expects" que tenham números (em inglês).

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
                         r"conference call|webcast|reconciliation|compensation cost|stock options|gross proceeds|"
                         r"offering|unrecognized|weighted[- ]average period|repurchase|dividend", re.I)
# marcas de um resultado já conhecido ("was", "compared with", "as of")...
PAST = re.compile(r"\b(was|were|landing|landed|exceeded|compared (with|to)|compares (with|to)|versus|vs\.|as (at|of)|reported|achieved|"
                  r"delivered|grew|declined|ended)\b", re.I)
# ...que só contam como previsão se houver também um verbo de previsão claro
FORWARD = re.compile(r"\b(expects?|expected to (be|grow|increase|decrease|range)|will|anticipates?|forecasts?|"
                     r"introduc\w*|establish\w*|initiat\w*|rais\w*|reaffirm\w*|reiterat\w*|updat\w*|narrow\w*|"
                     r"guidance (of|for|range of|is)|outlook (of|for|is))\b", re.I)
YEAR = re.compile(r"\b(?:fiscal\s+(?:year\s+)?|fy\s?|full[- ]year\s+)?(20\d\d)\b", re.I)


def identify(contact: str) -> str:
    """Identificação que a SEC pede em cada pedido: nome da ferramenta e email de contacto."""
    return f"bot-resultados {contact}"


def _get(url: str, user_agent: str) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}, timeout=30)
    r.raise_for_status()
    time.sleep(0.2)  # a SEC pede no máximo 10 pedidos por segundo
    return r


_cik_memo: dict = {}


def cik_table(user_agent: str) -> dict[str, int]:
    """Símbolo -> CIK da SEC, guardado 1x por dia (em memória e em data/sec_tickers.json)."""
    today = date.today().isoformat()
    if _cik_memo.get("dia") == today:
        return _cik_memo["cik"]
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
    _cik_memo.update(dia=today, cik=table)
    return table


def cik_for(symbol: str, user_agent: str) -> int | None:
    table = cik_table(user_agent)
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


def extract(text: str, limit: int = 3, max_chars: int = 260, year: int | None = None) -> list[str]:
    """Frases de previsão: palavra de previsão + período futuro + um número, sem texto jurídico.

    Deixa de fora frases sobre o passado: as que só falam de anos anteriores a `year` (por omissão, o ano
    atual) e as que descrevem um resultado já conhecido ("revenue was…", "compared with…") sem um verbo de
    previsão claro.
    """
    year = year or date.today().year
    found, seen = [], set()
    for block in text.split("\n"):
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", block.strip()):
            s = s.strip(" •·-–\t")
            if not (40 <= len(s) <= 600) or BOILERPLATE.search(s):
                continue
            if not (KEYWORDS.search(s) and NUMBER.search(s) and FUTURE.search(s)):
                continue
            years = [int(y) for y in YEAR.findall(s)]
            if years and max(years) < year:
                continue  # só anos que já passaram
            if PAST.search(s) and not FORWARD.search(s):
                continue  # resultado conhecido, não previsão
            key = re.sub(r"\W+", "", s.lower())[:60]
            if key in seen:
                continue
            seen.add(key)
            strong = bool(re.search(r"\b(outlook|guidance)\b", s, re.I)) + bool(re.search(r"\bexpect", s, re.I))
            found.append((-strong, len(found), s))
    best = sorted(found)[:limit]
    return [s if len(s) <= max_chars else s[:max_chars].rsplit(" ", 1)[0] + "…" for _, _, s in sorted(best, key=lambda x: x[1])]


# ---------------------------------------------------------------- feed de entregas recentes

FEED_URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&company=&dateb="
            "&owner=include&start={start}&count=100&output=atom")
ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)


def parse_feed(xml: str) -> list[dict]:
    """Entradas do feed Atom da SEC: CIK, formulário, pontos (items), hora de entrega e número de acesso."""
    out = []
    for body in ENTRY.findall(xml):
        title = re.search(r"<title>(.*?)</title>", body, re.S)
        updated = re.search(r"<updated>(.*?)</updated>", body)
        acc = re.search(r"accession-number=([\d-]+)", body)
        form = re.search(r'label="form type" term="([^"]+)"', body)
        cik = re.search(r"\((\d{10})\)", title.group(1) if title else "")
        if not (updated and acc and form and cik):
            continue
        summary = html.unescape(re.search(r"<summary[^>]*>(.*?)</summary>", body, re.S).group(1)) if "<summary" in body else ""
        out.append({"cik": int(cik.group(1)), "form": form.group(1), "accession": acc.group(1),
                    "items": re.findall(r"Item (\d+\.\d+)", summary),
                    "entregue": datetime.fromisoformat(updated.group(1)),
                    "nome": html.unescape(title.group(1).split(" - ", 1)[-1].split(" (")[0]).strip()})
    return out


def recent_results(user_agent: str, since: datetime, max_pages: int = 5) -> list[dict]:
    """Comunicados de resultados entregues desde `since`: 8-K com o ponto 2.02 e 6-K (empresas estrangeiras)."""
    out, seen = [], set()
    for form in ("8-K", "6-K"):
        for page in range(max_pages):
            entries = parse_feed(_get(FEED_URL.format(form=form, start=page * 100), user_agent).text)
            for e in entries:
                if e["entregue"] < since or e["accession"] in seen:
                    continue
                if e["form"].startswith("6-K") or "2.02" in e["items"]:
                    seen.add(e["accession"])
                    out.append(e)
            if len(entries) < 100 or entries[-1]["entregue"] < since:
                break  # chegámos às entregas que já tínhamos visto
    return out


def release_text(cik: int, accession: str, user_agent: str) -> tuple[str, str, str]:
    """(url, texto, html) do comunicado de uma entrega."""
    url, page = release_document({"cik": cik, "accession": accession, "primary": ""}, user_agent)
    return url, to_text(page), page


# ---------------------------------------------------------------- EPS do comunicado

_CUR = r"(?P<cur>US\$|HK\$|C\$|A\$|NT\$|R\$|RMB|\$|€|£)"
_AMT = _CUR + r"\s?(?P<neg>\()?\s?(?P<n>\d{1,3}(?:\.\d{1,4})?)\s?(?P<close>\))?"
_EPS_WORDS = (r"(?:\beps\b|(?:net\s+)?(?:earnings|income|loss|\(loss\)|income\s*\(loss\)|\(loss\)\s*income|profit)\s+"
              r"(?:from\s+continuing\s+operations\s+)?(?:attributable\s+to\s+[^$\n]{0,50}?)?per\s+"
              r"(?:diluted\s+|basic\s+and\s+diluted\s+|basic\s+)?(?:common\s+|ordinary\s+|class\s+a\s+(?:common\s+)?)?(?:share|ads))")
# "adjusted diluted EPS of $1.23", "EPS increased 10% to $1.23", tabela "Diluted EPS $ 1.23"
EPS_AFTER = re.compile(_EPS_WORDS + r"[^$\n%]{0,70}?(?:\d+(?:\.\d+)?\s?%[^$\n]{0,20}?)?" + _AMT, re.I)
# "net income of $50 million, or $1.23 per diluted share"
EPS_BEFORE = re.compile(r"\bor\s+" + _AMT + r"\s+(?:of\s+)?(?:(?:non-gaap|adjusted)\s+)?(?:diluted\s+)?(?:net\s+)?"
                        r"(?:earnings\s+|income\s+|loss\s+)?per\s+(?:diluted\s+|basic\s+and\s+diluted\s+)?(?:common\s+|ordinary\s+)?(?:share|ads)", re.I)
EPS_ADJUSTED = re.compile(r"non-gaap|adjusted|core\s+(?:eps|earnings|net)|\bnormalized\b|operating\s+eps", re.I)
# frases que falam de outra coisa por ação (dividendos, preços) ou do futuro
EPS_NOISE = re.compile(r"dividend|distribution|\bprice\b|par\s+value|repurchas|buyback|offering|valued|book\s+value|"
                       r"net\s+asset\s+value|\bnav\b|exclud\w*\s+(?:a\s+|an\s+)?\$|impact\s+of\s+\$|by\s+approximately|\breduced\b|"
                       r"expects?|expected|outlook|guidance|forecast|\brange\b|anticipat|project(?:s|ed|ion)|\btargets?\b|consensus|estimate", re.I)
EPS_LONG = re.compile(r"nine\s+months|six\s+months|twelve\s+months|full[- ]year|fiscal\s+(?:year|20\d\d)|year\s+ended|"
                      r"year[- ]to[- ]date|\bannual\b|\bfy\s?20?\d\d", re.I)
EPS_QUARTER = re.compile(r"quarter|three\s+months|\bq[1-4]\b", re.I)
EPS_PRIOR = re.compile(r"(compared|versus|\bvs\b|prior[- ]year|year[- ]ago|last\s+year|same\s+period|\bfrom\b)[^$]{0,30}$", re.I)
EPS_PRIOR_WIDE = re.compile(r"compared\s+(to|with)|versus|\bvs\b|prior[- ]year|year[- ]ago|same\s+period", re.I)


def eps_candidates(text: str) -> list[dict]:
    out = []
    for raw in text.split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or len(line) > 2000:
            continue
        for sent in re.split(r"(?<=[.;])\s+(?=[A-Z•])|•", line):
            if EPS_NOISE.search(sent):
                continue
            for rx in (EPS_AFTER, EPS_BEFORE):
                for m in rx.finditer(sent):
                    before = sent[:m.start("cur")]
                    if EPS_PRIOR.search(before[-60:]) or (rx is EPS_BEFORE and EPS_PRIOR_WIDE.search(before)):
                        continue  # valor do ano anterior
                    if before.rstrip().endswith("(") and re.search(r"\d\s*$", before.rstrip(" (")):
                        continue  # "RMB6.78 ($1.01)": conversão de moeda, não um prejuízo
                    if rx is EPS_BEFORE and not re.search(r"net\s+(income|earnings|loss)|earnings|net\s+\(loss\)", before, re.I):
                        continue
                    near = sent[max(0, m.start() - 25):m.end()]
                    if re.search(r"\bbasic\b", near, re.I) and not re.search(r"diluted", near, re.I):
                        continue  # só o básico: as estimativas são do diluído
                    lead = m.group(0) if rx is EPS_AFTER else before[-120:]
                    neg = bool(m.group("neg") and m.group("close")) or bool(
                        re.search(r"\bloss\b", lead, re.I) and not re.search(r"income|earnings|profit", lead, re.I))
                    v = float(m.group("n"))
                    ctx = sent[max(0, m.start() - 90):m.end()]
                    out.append({"eps": -v if neg else v, "ajustado": bool(EPS_ADJUSTED.search(ctx)),
                                "longo": bool(EPS_LONG.search(sent)), "trimestre": bool(EPS_QUARTER.search(sent)),
                                "frase": ctx[-170:].strip()})
    return out


def eps_from_release(text: str) -> dict | None:
    """EPS do trimestre lido do comunicado (o ajustado, se houver).

    Devolve None quando não há um valor claro (por exemplo, o trimestre e o ano aparecem sem se
    distinguir): é preferível esperar pelo Yahoo a avisar com o número errado.
    Testado em 126 comunicados reais (12 a 20/11/2026): leu um valor em 49 e, nesses, a decisão
    "bateu as estimativas" coincidiu com a do Yahoo em 44 (90%); 2 em 28 "bateu" foram falsos.
    """
    cands = [c for c in eps_candidates(text) if not c["longo"]]
    if not cands:
        return None
    pool = [c for c in cands if c["ajustado"]] or cands
    first = pool[0]
    if len({c["eps"] for c in pool[:3]}) > 1 and not first["trimestre"]:
        q = [c for c in pool[:3] if c["trimestre"]]
        if not q:
            return None
        first = q[0]
    return {k: first[k] for k in ("eps", "ajustado", "frase")}


_TABLE_LABEL = re.compile(
    r"^(?:(?P<adj>non-gaap|adjusted|core)\s+)?(?:diluted\s+)?(?:net\s+)?(?:earnings|income|\(loss\)|loss|income\s*\(loss\)|"
    r"\(loss\)\s*income|eps)(?:\s*\(loss\))?\s*(?:per\s+(?:common\s+|ordinary\s+)?(?:share|ads))?"
    r"(?:\s*[-–—,:]?\s*(?:diluted|assuming dilution))?\s*(?:\(\w\))?$", re.I)
_TABLE_SKIP = re.compile(r"outlook|guidance|forecast|expect|\branges?\b|\bto \$")
_TABLE_LONG = re.compile(r"nine months|six months|twelve months|year ended|fiscal year")


def _cells(row: str) -> list[str]:
    out = []
    for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I):
        t = html.unescape(re.sub(r"<[^>]+>", " ", c)).replace("\xa0", " ").replace("​", " ")
        out.append(re.sub(r"\s+", " ", t).strip())
    return out


def eps_from_tables(page: str) -> dict | None:
    """EPS lido das tabelas do comunicado, quando o rótulo e o valor estão em células separadas.

    Usa o primeiro valor da linha (o trimestre atual) e ignora tabelas de previsões, tabelas só de
    períodos longos, intervalos ("$2.20 to $2.30") e valores sem 2 casas decimais. É o complemento de
    eps_from_release(): nos 126 comunicados de teste acrescentou 16 leituras, 13 certas e nenhum falso "bateu".
    """
    found = []
    for table in re.findall(r"<table.*?</table>", page, re.S | re.I):
        tl = html.unescape(re.sub(r"<[^>]+>", " ", table)).lower()
        if _TABLE_SKIP.search(tl) or (_TABLE_LONG.search(tl) and not re.search(r"three months|quarter", tl)):
            continue
        for row in re.findall(r"<tr.*?</tr>", table, re.S | re.I):
            cs = [c for c in _cells(row) if c not in ("", "$", "US$")]
            if len(cs) < 2:
                continue
            label = re.sub(r"[\(\[]\d[\)\]]|\*", "", cs[0]).strip(" :")
            m = _TABLE_LABEL.match(label)
            if not m or not re.search(r"diluted|per share|eps|per ads", cs[0], re.I):
                continue
            if re.search(r"basic", cs[0], re.I) and not re.search(r"diluted", cs[0], re.I):
                continue
            values = " ".join(cs[1:])
            if re.search(r"\bto\b|—|–", values):
                continue
            nums = [(-float(v.group(2)) if v.group(1) and v.group(3) else float(v.group(2)))
                    for v in re.finditer(r"(\()?\s*\$?\s*(?<![\d.])(\d{1,3}\.\d{2})(?!\d)\s*(\))?", values)]
            if nums:
                found.append({"eps": nums[0], "ajustado": bool(m.group("adj")) or bool(re.search(r"non-gaap|adjusted", tl[:300])),
                              "frase": f"{cs[0]} {values[:60]}"})
    if not found:
        return None
    return ([f for f in found if f["ajustado"]] or found)[0]


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
    import bot
    contact = bot.load_config().get("sec_contacto")
    if not contact:
        sys.exit("Falta o email de contacto para a SEC: segredo SEC_CONTACTO no GitHub "
                 "ou 'sec_contacto' em config.local.json.")
    out = guidance_for(sys.argv[1], identify(contact))
    print(json.dumps(out, ensure_ascii=False, indent=1))
