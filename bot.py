"""Bot de resultados trimestrais (earnings).

Comandos:
  python bot.py manha      -> resumo dos próximos relatórios
  python bot.py verificar  -> procura resultados novos e avisa os positivos
  python bot.py premarket  -> resumo dos positivos antes da abertura de Nova Iorque
  python bot.py rever      -> mostra o que as ações fizeram depois dos alertas
  python bot.py teste      -> envia uma notificação de teste
  python bot.py auto       -> faz o que for preciso consoante a hora (é este que o agendador corre)
Acrescenta --seco para mostrar no ecrã em vez de enviar para o telemóvel.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CONFIG_FILE = ROOT / "config.json"
EURONEXT_FILE = ROOT / "euronext.txt"
STATE_FILE = DATA / "estado.json"
LOG_FILE = DATA / "registo.csv"
ERROR_LOG = DATA / "erros.log"

LIS = ZoneInfo("Europe/Lisbon")
NY = ZoneInfo("America/New_York")
LOOKBACK_DAYS = 3

DEFAULTS = {
    "ntfy_topico": "",
    "ntfy_servidor": "https://ntfy.sh",
    "capitalizacao_minima_usd": 2_000_000_000,
    "surpresa_eps_minima_pct": 5,
    "movimento_preco_minimo_pct": 2,
    "alertar_bateu_mas_cai": False,
    "hora_resumo_manha": "08:30",
    "minutos_antes_abertura_resumo": 30,
    "dias_calendario": 3,
    "max_empresas_por_dia_no_resumo": 15,
}

LOG_COLUMNS = [
    "registado_em", "simbolo", "nome", "mercado", "data_resultados", "momento",
    "eps_estimado", "eps_real", "surpresa_pct", "movimento_pct", "movimento_tipo",
    "positivo", "alertado", "prox_trim_eps", "prox_trim_receitas",
    "revisoes_sobe_7d", "revisoes_desce_7d",
    "dia_reacao", "fecho_anterior", "abertura", "maximo", "fecho",
    "abertura_vs_fecho_ant_pct", "fecho_vs_abertura_pct", "maximo_vs_abertura_pct",
    "atraso_min",  # minutos entre a hora marcada do relatório e o bot o ver publicado no Yahoo
]

MOMENTO_PT = {"BMO": "antes da abertura", "AMC": "depois do fecho", "TNS": "hora não indicada"}


# ---------------------------------------------------------------- utilitários

def num(x) -> float | None:
    """Converte para float; devolve None para vazios/NaN."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def fmt_pct(v: float | None, sinal: bool = True) -> str:
    if v is None:
        return "?"
    return f"{v:+.1f}%" if sinal else f"{v:.1f}%"


def fmt_big(v: float | None) -> str:
    if v is None:
        return "?"
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.1f} mil M"
    if a >= 1e6:
        return f"{v / 1e6:.0f} M"
    return f"{v:,.0f}"


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    # config.local.json não vai para o GitHub: é onde fica o tópico ntfy no PC
    for path in (CONFIG_FILE, ROOT / "config.local.json"):
        if path.exists():
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
    if os.environ.get("NTFY_TOPICO"):  # na nuvem vem de um segredo do GitHub
        cfg["ntfy_topico"] = os.environ["NTFY_TOPICO"].strip()
    return cfg


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_state(state: dict) -> None:
    DATA.mkdir(exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(STATE_FILE)


def euronext_symbols() -> list[str]:
    if not EURONEXT_FILE.exists():
        return []
    out = []
    for line in EURONEXT_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.upper())
    return out


def hhmm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


# ---------------------------------------------------------------- notificações

def notify(cfg: dict, title: str, message: str, *, tags=None, priority=3,
           click: str | None = None, actions=None, dry: bool = False) -> None:
    if not dry and not cfg.get("ntfy_topico"):
        raise RuntimeError("Falta o tópico ntfy (config.local.json ou variável NTFY_TOPICO).")
    if dry:
        print(f"\n=== {title} ===\n{message}")
        if click:
            print(f"[link] {click}")
        for a in actions or []:
            print(f"[{a['label']}] {a['url']}")
        return
    payload = {
        "topic": cfg["ntfy_topico"],
        "title": title,
        "message": message[:3900],
        "priority": priority,
    }
    if tags:
        payload["tags"] = tags
    if click:
        payload["click"] = click
    if actions:
        payload["actions"] = [{"action": "view", "label": a["label"], "url": a["url"]} for a in actions]
    r = requests.post(cfg["ntfy_servidor"].rstrip("/"), json=payload, timeout=20)
    r.raise_for_status()


# ---------------------------------------------------------------- fontes de dados

def us_calendar(start: date, end_inclusive: date, min_cap: float) -> list[dict]:
    """Relatórios de ações americanas (NASDAQ/NYSE) via calendário do Yahoo."""
    cal = yf.Calendars()
    events: list[dict] = []
    end = end_inclusive + timedelta(days=1)  # o fim do Yahoo é exclusivo
    for offset in range(0, 3000, 100):
        df = cal.get_earnings_calendar(
            market_cap=min_cap, filter_most_active=False,
            start=start.isoformat(), end=end.isoformat(),
            limit=100, offset=offset, force=True,
        )
        if df is None or df.empty:
            break
        for sym, row in df.iterrows():
            when = pd.Timestamp(row["Event Start Date"])
            if when.tzinfo is None:
                when = when.tz_localize("UTC")
            mom = str(row.get("Timing") or "")
            if mom not in ("BMO", "AMC"):  # TAS/TNS: deduz pela hora de Nova Iorque
                ny = when.tz_convert(NY)
                mom = "BMO" if (ny.hour, ny.minute) < (9, 30) else ("AMC" if ny.hour >= 16 else "TNS")
            when = when.tz_convert(LIS)
            events.append({
                "simbolo": str(sym),
                "nome": str(row.get("Company") or sym),
                "mercado": "EUA",
                "capitalizacao": num(row.get("Marketcap")),
                "quando": when.isoformat(),
                "dia": when.date().isoformat(),
                "momento": mom,
                "eps_estimado": num(row.get("EPS Estimate")),
                "eps_real": num(row.get("Reported EPS")),
                "surpresa_pct": num(row.get("Surprise(%)")),
            })
        if len(df) < 100:
            break
    return events


def nasdaq_calendar(day: date, min_cap: float) -> list[dict]:
    """Alternativa caso o Yahoo falhe: calendário da Nasdaq (sem resultados reais)."""
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    r = requests.get(f"https://api.nasdaq.com/api/calendar/earnings?date={day.isoformat()}",
                     headers=headers, timeout=20)
    r.raise_for_status()
    rows = ((r.json().get("data") or {}).get("rows")) or []
    timing = {"time-pre-market": "BMO", "time-after-hours": "AMC"}
    out = []
    for row in rows:
        cap = num((row.get("marketCap") or "").replace("$", "").replace(",", ""))
        if cap is None or cap < min_cap:
            continue
        eps = (row.get("epsForecast") or "").replace("$", "").replace("(", "-").replace(")", "")
        mom = timing.get(row.get("time"), "TNS")
        hour = {"BMO": 13, "AMC": 21}.get(mom, 12)
        out.append({
            "simbolo": row["symbol"], "nome": row.get("name") or row["symbol"], "mercado": "EUA",
            "capitalizacao": cap,
            "quando": datetime(day.year, day.month, day.day, hour, 0, tzinfo=LIS).isoformat(),
            "dia": day.isoformat(), "momento": mom,
            "eps_estimado": num(eps), "eps_real": None, "surpresa_pct": None,
        })
    return out


def listed_symbols(today: date) -> dict[str, str]:
    """Ações cotadas na NASDAQ e NYSE (lista oficial da Nasdaq), guardada 1x por dia."""
    cache = DATA / "cotadas.json"
    if cache.exists():
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("dia") == today.isoformat():
            return c["simbolos"]
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    names = {"nasdaq": "NASDAQ", "nyse": "NYSE", "amex": "NYSE American"}
    out: dict[str, str] = {}
    try:
        for ex, label in names.items():
            r = requests.get(f"https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&exchange={ex}",
                             headers=headers, timeout=60)
            r.raise_for_status()
            for row in r.json()["data"]["table"]["rows"]:
                out[row["symbol"].strip().replace("/", "-")] = label
    except Exception:
        log_error("Lista de ações cotadas falhou")
        if cache.exists():  # usa a de ontem
            return json.loads(cache.read_text(encoding="utf-8"))["simbolos"]
        return {}
    DATA.mkdir(exist_ok=True)
    cache.write_text(json.dumps({"dia": today.isoformat(), "simbolos": out}), encoding="utf-8")
    return out


def us_events(start: date, end_inclusive: date, min_cap: float) -> list[dict]:
    """Relatórios de ações cotadas na NASDAQ/NYSE (exclui balcão/OTC)."""
    listed = listed_symbols(datetime.now(LIS).date())
    evs = _us_events(start, end_inclusive, min_cap)
    if not listed:
        return evs
    out = []
    for e in evs:
        if e["simbolo"] in listed:
            e["mercado"] = listed[e["simbolo"]]
            out.append(e)
    return out


def _us_events(start: date, end_inclusive: date, min_cap: float) -> list[dict]:
    try:
        return us_calendar(start, end_inclusive, min_cap)
    except Exception:
        log_error("Calendário Yahoo falhou, a usar Nasdaq")
        out = []
        d = start
        while d <= end_inclusive:
            try:
                out += nasdaq_calendar(d, min_cap)
            except Exception:
                log_error(f"Calendário Nasdaq falhou para {d}")
            d += timedelta(days=1)
        return out


def eu_events_for(symbol: str, limit: int = 6) -> list[dict]:
    """Datas de resultados (passadas e futuras) de uma ação europeia."""
    t = yf.Ticker(symbol)
    df = t.get_earnings_dates(limit=limit)
    if df is None or df.empty:
        return []
    out = []
    for ts, row in df.iterrows():
        when = pd.Timestamp(ts)
        if when.tzinfo is None:
            when = when.tz_localize(NY)
        when = when.tz_convert(LIS)
        h = when.hour
        mom = "BMO" if h < 9 else ("AMC" if h >= 17 else "TNS")
        out.append({
            "simbolo": symbol, "nome": symbol, "mercado": "Euronext", "capitalizacao": None,
            "quando": when.isoformat(), "dia": when.date().isoformat(), "momento": mom,
            "eps_estimado": num(row.get("EPS Estimate")),
            "eps_real": num(row.get("Reported EPS")),
            "surpresa_pct": num(row.get("Surprise(%)")),
        })
    return out


def refresh_eu(state: dict, today: date, days: int) -> list[dict]:
    """Uma vez por dia: guarda os relatórios europeus de ontem até daqui a `days` dias."""
    if state.get("eu_atualizado") == today.isoformat():
        return state.get("eu_eventos", [])
    lo, hi = today - timedelta(days=LOOKBACK_DAYS), today + timedelta(days=days)
    keep = []
    for sym in euronext_symbols():
        try:
            for ev in eu_events_for(sym):
                if lo.isoformat() <= ev["dia"] <= hi.isoformat():
                    keep.append(ev)
        except Exception:
            log_error(f"Euronext: falhou {sym}")
    state["eu_eventos"] = keep
    state["eu_atualizado"] = today.isoformat()
    return keep


def price_move(info: dict, ev: dict) -> tuple[float | None, str]:
    """Reação do preço ao relatório.

    Devolve (None, ...) se ainda não houve negociação depois do relatório (tenta-se mais tarde)
    e o tipo 'tardio' se a sessão de reação já acabou (já não serve para negociar no dia).
    """
    st = (info.get("marketState") or "").upper()
    pre, post = num(info.get("preMarketChangePercent")), num(info.get("postMarketChangePercent"))
    reg = num(info.get("regularMarketChangePercent"))
    try:
        tz = ZoneInfo(info.get("exchangeTimezoneName") or "America/New_York")
    except Exception:
        tz = NY
    ev_dt = datetime.fromisoformat(ev["quando"]).astimezone(tz)
    # sessão de reação: a do próprio dia, ou a seguinte se o relatório saiu depois do fecho
    reaction = ev_dt.date() + timedelta(days=1 if ev_dt.hour >= 16 else 0)
    while reaction.weekday() >= 5:
        reaction += timedelta(days=1)
    last_ts = num(info.get("regularMarketTime"))
    if last_ts is None:
        return None, "sem preço"
    last_session = datetime.fromtimestamp(last_ts, tz).date()
    if last_session > reaction:
        return reg, "tardio"
    if last_session == reaction:
        return (reg, "sessão") if st == "REGULAR" else (reg, "tardio")
    # ainda não houve sessão normal depois do relatório
    if st.startswith("PRE"):
        return pre, "pre-market"
    return post, "after-hours"


def expectations(t: yf.Ticker) -> dict:
    """Expectativas dos analistas para o próximo trimestre e revisões recentes."""
    out: dict = {}
    try:
        ee = t.earnings_estimate
        if ee is not None and "+1q" in ee.index:
            out["prox_trim_eps"] = num(ee.loc["+1q", "avg"])
            out["prox_trim_eps_cresc"] = num(ee.loc["+1q", "growth"])
        if ee is not None and "+1y" in ee.index:
            out["prox_ano_eps_cresc"] = num(ee.loc["+1y", "growth"])
    except Exception:
        pass
    try:
        re_ = t.revenue_estimate
        if re_ is not None and "+1q" in re_.index:
            out["prox_trim_receitas"] = num(re_.loc["+1q", "avg"])
            out["prox_trim_receitas_cresc"] = num(re_.loc["+1q", "growth"])
    except Exception:
        pass
    try:
        rv = t.eps_revisions
        if rv is not None and "+1q" in rv.index:
            out["revisoes_sobe_7d"] = num(rv.loc["+1q", "upLast7days"])
            out["revisoes_desce_7d"] = num(rv.loc["+1q", "downLast7days"])
    except Exception:
        pass
    try:
        tr = t.eps_trend
        if tr is not None and "+1q" in tr.index:
            cur, ago = num(tr.loc["+1q", "current"]), num(tr.loc["+1q", "7daysAgo"])
            if cur is not None and ago:
                out["prox_trim_eps_var_7d"] = (cur / ago - 1) * 100
    except Exception:
        pass
    return out


def links(ev: dict) -> tuple[str, list[dict]]:
    sym = ev["simbolo"]
    yahoo = f"https://finance.yahoo.com/quote/{sym}/"
    acts = [{"label": "Yahoo", "url": yahoo}]
    if ev["mercado"] != "Euronext":
        acts.insert(0, {"label": "Comunicado (SEC)",
                        "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={sym}&owner=include&count=10"})
    return yahoo, acts


# ---------------------------------------------------------------- registo

def append_log(row: dict) -> None:
    DATA.mkdir(exist_ok=True)
    new = not LOG_FILE.exists()
    if not new:  # se entretanto foram acrescentadas colunas, atualiza o cabeçalho do ficheiro
        with LOG_FILE.open(encoding="utf-8-sig", newline="") as f:
            old = list(csv.DictReader(f, delimiter=";"))
            header = list(old[0].keys()) if old else LOG_COLUMNS
        if header != LOG_COLUMNS:
            with LOG_FILE.open("w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=LOG_COLUMNS, delimiter=";")
                w.writeheader()
                for r in old:
                    w.writerow({k: r.get(k, "") for k in LOG_COLUMNS})
    with LOG_FILE.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLUMNS, delimiter=";")
        if new:
            w.writeheader()
        w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in LOG_COLUMNS})


def log_error(msg: str) -> None:
    DATA.mkdir(exist_ok=True)
    with ERROR_LOG.open("a", encoding="utf-8") as f:
        f.write(f"\n[{datetime.now(LIS):%Y-%m-%d %H:%M}] {msg}\n{traceback.format_exc()}")
    if sys.stderr is not None:  # na nuvem aparece no registo da corrida
        print(f"ERRO: {msg}\n{traceback.format_exc()}", file=sys.stderr)


# ---------------------------------------------------------------- tarefas

def task_morning(cfg: dict, state: dict, now: datetime, dry: bool) -> None:
    today = now.date()
    days = int(cfg["dias_calendario"])
    us = us_events(today, today + timedelta(days=days), cfg["capitalizacao_minima_usd"])
    eu = [e for e in refresh_eu(state, today, days) if e["dia"] >= today.isoformat()]
    allev = us + eu
    print(f"manha: {len(us)} relatórios NASDAQ/NYSE, {len(eu)} Euronext")
    if not dry:  # a dashboard lê este ficheiro
        DATA.mkdir(exist_ok=True)
        (DATA / "calendario.json").write_text(
            json.dumps({"gerado_em": now.isoformat(), "eventos": allev}, ensure_ascii=False), encoding="utf-8")
    if not allev:
        notify(cfg, "Resultados: nada nos próximos dias", "Sem relatórios na lista.", tags=["calendar"], dry=dry)
        return
    by_day: dict[str, list[dict]] = {}
    for e in allev:
        by_day.setdefault(e["dia"], []).append(e)
    lines = []
    maxn = int(cfg["max_empresas_por_dia_no_resumo"])
    for d in sorted(by_day):
        evs = sorted(by_day[d], key=lambda e: -(e["capitalizacao"] or 0))
        dd = date.fromisoformat(d)
        lines.append(f"\n📅 {dd:%d/%m} ({['seg','ter','qua','qui','sex','sáb','dom'][dd.weekday()]}) · {len(evs)} empresas")
        for e in evs[:maxn]:
            hora = datetime.fromisoformat(e["quando"]).strftime("%H:%M")
            mom = {"BMO": "🌅", "AMC": "🌙"}.get(e["momento"], "•")
            est = f" · EPS est. {e['eps_estimado']:.2f}" if e["eps_estimado"] is not None else ""
            cap = f" · {fmt_big(e['capitalizacao'])}$" if e["capitalizacao"] else ""
            lines.append(f"{mom} {e['simbolo']} {hora}{est}{cap}")
        if len(evs) > maxn:
            lines.append(f"  … e mais {len(evs) - maxn}")
    lines.append("\n🌅 antes da abertura · 🌙 depois do fecho (horas de Lisboa)")
    notify(cfg, f"Resultados: próximos {days} dias", "\n".join(lines).strip(), tags=["calendar"], dry=dry)


def evaluate(cfg: dict, ev: dict) -> dict:
    t = yf.Ticker(ev["simbolo"])
    try:
        info = t.info or {}
    except Exception:
        info = {}
    move, move_kind = price_move(info, ev)
    if move is None:
        return None  # ainda sem preço depois do relatório: volta a tentar na próxima corrida
    exp = expectations(t)
    name = info.get("shortName") or info.get("longName") or ev["nome"]
    surprise = ev["surpresa_pct"]
    if surprise is None and ev["eps_real"] is not None and ev["eps_estimado"]:
        surprise = (ev["eps_real"] - ev["eps_estimado"]) / abs(ev["eps_estimado"]) * 100
    late = move_kind == "tardio"
    beat = surprise is not None and surprise >= cfg["surpresa_eps_minima_pct"]
    up = move >= cfg["movimento_preco_minimo_pct"]
    return {**ev, **exp, "nome": name, "surpresa_pct": surprise, "movimento_pct": move,
            "movimento_tipo": move_kind, "positivo": beat and up and not late,
            "bateu_mas_cai": beat and not up and not late}


def alert_text(r: dict) -> tuple[str, str]:
    icon = "🟢" if r["positivo"] else "⚠️"
    title = f"{icon} {r['simbolo']} bateu {fmt_pct(r['surpresa_pct'])} · {r['movimento_tipo']} {fmt_pct(r['movimento_pct'])}"
    lines = [
        f"{r['nome']} ({r['mercado']}) · {MOMENTO_PT.get(r['momento'], '')}",
        f"EPS: {r['eps_real']:.2f} vs {r['eps_estimado']:.2f} estimado ({fmt_pct(r['surpresa_pct'])})"
        if r["eps_real"] is not None and r["eps_estimado"] is not None else "EPS: ?",
        f"Preço agora: {fmt_pct(r['movimento_pct'])} ({r['movimento_tipo']})",
    ]
    exp = []
    if r.get("prox_trim_eps") is not None:
        g = r.get("prox_trim_eps_cresc")
        exp.append(f"EPS {r['prox_trim_eps']:.2f}" + (f" ({fmt_pct(g * 100)} a/a)" if g is not None else ""))
    if r.get("prox_trim_receitas") is not None:
        g = r.get("prox_trim_receitas_cresc")
        exp.append(f"receitas {fmt_big(r['prox_trim_receitas'])}" + (f" ({fmt_pct(g * 100)} a/a)" if g is not None else ""))
    if exp:
        lines.append("Próx. trimestre (analistas): " + ", ".join(exp))
    up, down = r.get("revisoes_sobe_7d"), r.get("revisoes_desce_7d")
    if up is not None or down is not None:
        lines.append(f"Revisões 7 dias: {int(up or 0)} ↑ / {int(down or 0)} ↓"
                     + (f" (estimativa {fmt_pct(r['prox_trim_eps_var_7d'])})" if r.get("prox_trim_eps_var_7d") is not None else ""))
    lines.append("Guidance: lê a secção 'Outlook' no comunicado.")
    return title, "\n".join(lines)


def task_check(cfg: dict, state: dict, now: datetime, dry: bool) -> None:
    today = now.date()
    since = today - timedelta(days=LOOKBACK_DAYS)  # cobre o fim de semana e dias com o PC desligado
    seen: dict = state.setdefault("vistos", {})
    positives: dict = state.setdefault("positivos", {})

    candidates = [e for e in us_events(since, today, cfg["capitalizacao_minima_usd"])
                  if e["eps_real"] is not None]
    for ev in refresh_eu(state, today, int(cfg["dias_calendario"])):
        if since.isoformat() <= ev["dia"] <= today.isoformat():
            key = f"{ev['simbolo']}|{ev['dia']}"
            if key in seen:
                continue
            try:  # atualiza: o resultado real pode já ter saído
                fresh = [x for x in eu_events_for(ev["simbolo"], limit=4) if x["dia"] == ev["dia"]]
                if fresh and fresh[0]["eps_real"] is not None:
                    candidates.append(fresh[0])
            except Exception:
                log_error(f"Euronext: falhou atualização de {ev['simbolo']}")

    for ev in candidates:
        key = f"{ev['simbolo']}|{ev['dia']}"
        if key in seen:
            continue
        try:
            r = evaluate(cfg, ev)
        except Exception:
            log_error(f"Falhou avaliação de {key}")
            continue
        if r is None:
            continue
        send = r["positivo"] or (r["bateu_mas_cai"] and cfg["alertar_bateu_mas_cai"])
        if send:
            title, body = alert_text(r)
            click, acts = links(r)
            notify(cfg, title, body, tags=["chart_with_upwards_trend"] if r["positivo"] else ["warning"],
                   priority=4 if r["positivo"] else 3, click=click, actions=acts, dry=dry)
        if r["positivo"]:
            positives[key] = {"simbolo": r["simbolo"], "nome": r["nome"], "mercado": r["mercado"],
                              "dia": r["dia"], "quando": r["quando"], "momento": r["momento"],
                              "surpresa_pct": r["surpresa_pct"]}
        if not dry:
            delay = round((now - datetime.fromisoformat(r["quando"])).total_seconds() / 60)
            append_log({**r, "atraso_min": delay, "registado_em": now.strftime("%Y-%m-%d %H:%M"), "data_resultados": r["dia"],
                        "positivo": "sim" if r["positivo"] else "não", "alertado": "sim" if send else "não"})
        seen[key] = now.isoformat()

    new = sum(1 for e in candidates if seen.get(f"{e['simbolo']}|{e['dia']}") == now.isoformat())
    print(f"verificar: {len(candidates)} resultados publicados desde {since}, {new} novos, "
          f"{len(listed_symbols(today))} ações cotadas na lista")

    # limpa entradas com mais de 10 dias
    cutoff = (today - timedelta(days=10)).isoformat()
    for d in (seen, positives):
        for k in [k for k in d if k.split("|")[1] < cutoff]:
            del d[k]


def task_premarket(cfg: dict, state: dict, now: datetime, dry: bool) -> None:
    """Antes da abertura de NY: positivos de ontem (depois do fecho) e de hoje (antes da abertura)."""
    today, yesterday = now.date().isoformat(), (now.date() - timedelta(days=1)).isoformat()
    sel = [p for p in state.get("positivos", {}).values()
           if p["mercado"] != "Euronext" and p.get("quando")
           and (p["dia"] == today or (p["dia"] == yesterday and p["momento"] == "AMC"))]
    if not sel:
        return
    lines = []
    for p in sel:
        try:
            move, kind = price_move(yf.Ticker(p["simbolo"]).info or {}, p)
        except Exception:
            move, kind = None, "?"
        lines.append(f"{p['simbolo']}: EPS {fmt_pct(p['surpresa_pct'])} · {kind} {fmt_pct(move)}")
    opens = ny_open(now).astimezone(LIS)
    notify(cfg, f"Antes da abertura de Nova Iorque ({opens:%H:%M})", "\n".join(lines),
           tags=["bell"], priority=4, dry=dry)


def ny_open(now: datetime) -> datetime:
    """Hora de abertura de Nova Iorque no dia de hoje (09:30 de lá)."""
    return now.astimezone(NY).replace(hour=9, minute=30, second=0, microsecond=0)


def task_review() -> None:
    """Preenche no registo o que a ação fez no dia de reação e mostra um resumo."""
    if not LOG_FILE.exists():
        print("Ainda não há registo.")
        return
    with LOG_FILE.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    today = datetime.now(LIS).date()
    for r in rows:
        if r.get("fecho"):
            continue
        d = date.fromisoformat(r["data_resultados"])
        try:
            hist = yf.Ticker(r["simbolo"]).history(start=d - timedelta(days=7), end=d + timedelta(days=7), auto_adjust=False)
        except Exception:
            continue
        if hist is None or hist.empty:
            continue
        hist.index = [i.date() for i in hist.index]
        after = [i for i in hist.index if (i > d if r["momento"] == "AMC" else i >= d)]
        before = [i for i in hist.index if i < (after[0] if after else d)]
        if not after or not before or after[0] >= today and datetime.now(LIS).hour < 22:
            continue
        day, prev = after[0], before[-1]
        pc, o, h, c = (float(hist.loc[prev, "Close"]), float(hist.loc[day, "Open"]),
                       float(hist.loc[day, "High"]), float(hist.loc[day, "Close"]))
        r.update({"dia_reacao": day.isoformat(), "fecho_anterior": f"{pc:.4f}", "abertura": f"{o:.4f}",
                  "maximo": f"{h:.4f}", "fecho": f"{c:.4f}",
                  "abertura_vs_fecho_ant_pct": f"{(o / pc - 1) * 100:.2f}",
                  "fecho_vs_abertura_pct": f"{(c / o - 1) * 100:.2f}",
                  "maximo_vs_abertura_pct": f"{(h / o - 1) * 100:.2f}"})
    with LOG_FILE.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLUMNS, delimiter=";")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in LOG_COLUMNS})

    done = [r for r in rows if r.get("fecho")]
    print(f"Registos: {len(rows)} · com dia de reação fechado: {len(done)}\n")
    print(f"{'símbolo':8} {'dia':10} {'pos':4} {'surpr.':>7} {'gap':>7} {'ab→fecho':>9} {'ab→máx':>8}")
    for r in done[-40:]:
        print(f"{r['simbolo']:8} {r['dia_reacao']:10} {r['positivo']:4} {float(r['surpresa_pct'] or 0):>6.1f}% "
              f"{float(r['abertura_vs_fecho_ant_pct']):>6.1f}% {float(r['fecho_vs_abertura_pct']):>8.1f}% "
              f"{float(r['maximo_vs_abertura_pct']):>7.1f}%")
    pos = [r for r in done if r["positivo"] == "sim"]
    if pos:
        oc = [float(r["fecho_vs_abertura_pct"]) for r in pos]
        hi = [float(r["maximo_vs_abertura_pct"]) for r in pos]
        print(f"\nPositivos ({len(pos)}): comprar na abertura e vender no fecho deu em média {sum(oc) / len(oc):+.2f}%;"
              f" subiram no fecho em {sum(1 for x in oc if x > 0)}/{len(oc)};"
              f" chegaram a +5% acima da abertura em {sum(1 for x in hi if x >= 5)}/{len(hi)}.")


# ---------------------------------------------------------------- main

def main() -> None:
    if sys.stdout is not None:  # em segundo plano (pythonw) não há consola
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comando", choices=["manha", "verificar", "premarket", "rever", "teste", "auto"])
    ap.add_argument("--seco", action="store_true", help="mostra no ecrã em vez de enviar")
    a = ap.parse_args()

    cfg, state = load_config(), load_state()
    now = datetime.now(LIS)
    try:
        if a.comando == "teste":
            notify(cfg, "Teste do bot de resultados ✅", "Se estás a ver isto no telemóvel, está tudo a funcionar.",
                   tags=["white_check_mark"], dry=a.seco)
        elif a.comando == "manha":
            task_morning(cfg, state, now, a.seco)
        elif a.comando == "verificar":
            task_check(cfg, state, now, a.seco)
        elif a.comando == "premarket":
            task_premarket(cfg, state, now, a.seco)
        elif a.comando == "rever":
            task_review()
        elif a.comando == "auto":
            today = now.date().isoformat()
            weekday = now.weekday() < 5
            failed = False

            def step(name: str, fn, flag: str | None = None) -> None:
                """Uma tarefa que falha não impede as outras; volta a tentar na próxima corrida."""
                nonlocal failed
                try:
                    fn()
                    if flag:
                        state[flag] = today
                except Exception:
                    failed = True
                    log_error(f"Erro na tarefa {name}")

            if weekday and (now.hour, now.minute) >= hhmm(cfg["hora_resumo_manha"]) and state.get("ultimo_resumo_manha") != today:
                step("manha", lambda: task_morning(cfg, state, now, a.seco), "ultimo_resumo_manha")
            # verifica de seg a sex, e sábado de madrugada (relatórios de sexta à noite)
            if weekday or (now.weekday() == 5 and now.hour < 2):
                step("verificar", lambda: task_check(cfg, state, now, a.seco))
            opens = ny_open(now)
            before = timedelta(minutes=int(cfg["minutos_antes_abertura_resumo"]))
            if weekday and opens - before <= now < opens and state.get("ultimo_resumo_premarket") != today:
                step("premarket", lambda: task_premarket(cfg, state, now, a.seco), "ultimo_resumo_premarket")
            # revisão: todas as noites depois do fecho de Nova Iorque, e ao sábado de manhã
            if ((weekday and now.hour >= 22) or now.weekday() == 5) and state.get("ultima_revisao") != today:
                step("rever", task_review, "ultima_revisao")
            if failed:
                sys.exit(1)
    except Exception:
        log_error(f"Erro no comando {a.comando}")
        raise
    finally:
        if not a.seco:  # um teste a seco não marca nada como já avisado
            save_state(state)


if __name__ == "__main__":
    main()
