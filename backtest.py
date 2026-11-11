"""Teste histórico: o que fizeram as ações depois de apresentarem resultados?

Para cada ação da NASDAQ/NYSE acima de uma capitalização mínima, vai buscar os relatórios dos
últimos 2 anos (EPS real vs estimado) e mede a sessão de reação: abertura face ao fecho anterior
(gap), abertura -> fecho, abertura -> máximo e abertura -> mínimo.

Limitações: só ações que existem hoje (as que saíram da bolsa não entram); o preço de pre-market
histórico não existe de graça, por isso o gap da abertura faz de "ação a subir"; os preços são
diários, logo não se sabe se o máximo veio antes ou depois do mínimo.

Uso:  python backtest.py [--max N] [--cap-min USD] [--anos 2]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
NY = "America/New_York"
SURPRISES = [None, 0, 5, 10, 20]   # surpresa mínima do EPS em %; None = qualquer resultado
GAPS = [None, 0, 2, 5]             # subida mínima na abertura em %; None = qualquer


def universe(min_cap: float) -> dict[str, tuple[str, float]]:
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    out: dict[str, tuple[str, float]] = {}
    for ex, label in {"nasdaq": "NASDAQ", "nyse": "NYSE", "amex": "NYSE American"}.items():
        r = requests.get(f"https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&exchange={ex}",
                         headers=headers, timeout=60)
        r.raise_for_status()
        for row in r.json()["data"]["table"]["rows"]:
            try:
                cap = float((row.get("marketCap") or "0").replace(",", "") or 0)
            except ValueError:
                cap = 0
            if cap >= min_cap:
                out[row["symbol"].strip().replace("/", "-")] = (label, cap)
    return out


def earnings(sym: str, since: date) -> list[dict] | None:
    """Relatórios passados com EPS real e estimado. None = pedido falhou."""
    df = None
    for attempt in range(3):
        try:
            df = yf.Ticker(sym).get_earnings_dates(limit=12)
            break
        except Exception:
            if attempt == 2:
                return None
            time.sleep(10 * (attempt + 1))
    if df is None or df.empty:
        return []
    out = []
    for ts, row in df.iterrows():
        ts = pd.Timestamp(ts)
        ts = ts.tz_localize(NY) if ts.tzinfo is None else ts.tz_convert(NY)
        est, rep, sur = row.get("EPS Estimate"), row.get("Reported EPS"), row.get("Surprise(%)")
        if pd.isna(est) or pd.isna(rep) or ts.date() < since or ts.date() >= date.today():
            continue
        if pd.isna(sur):
            if est == 0:
                continue
            sur = (rep - est) / abs(est) * 100
        if (ts.hour, ts.minute) < (9, 30):
            mom = "BMO"
        elif ts.hour >= 16:
            mom = "AMC"
        else:
            continue  # saiu a meio da sessão: não dá para medir a reação com preços diários
        out.append({"simbolo": sym, "data": ts.date(), "momento": mom,
                    "eps_estimado": float(est), "eps_real": float(rep), "surpresa_pct": float(sur)})
    return out


def reactions(events: list[dict], batch: int = 100) -> list[dict]:
    """Junta a cada relatório os preços da sessão de reação."""
    by_sym: dict[str, list[dict]] = {}
    for e in events:
        by_sym.setdefault(e["simbolo"], []).append(e)
    syms, out = sorted(by_sym), []
    for i in range(0, len(syms), batch):
        chunk = syms[i:i + batch]
        try:
            px = yf.download(chunk, period="3y", interval="1d", auto_adjust=True,
                             group_by="ticker", threads=True, progress=False)
        except Exception as exc:
            print(f"preços: lote {i} falhou ({exc})", file=sys.stderr)
            continue
        for s in chunk:
            try:
                df = px[s].dropna(subset=["Open", "Close"]) if isinstance(px.columns, pd.MultiIndex) else px.dropna()
            except KeyError:
                continue
            if len(df) < 5:
                continue
            days = np.array([d.date() for d in df.index])
            for e in by_sym[s]:
                pos = int(np.searchsorted(days, e["data"], side="left" if e["momento"] == "BMO" else "right"))
                if pos < 1 or pos >= len(days) or (days[pos] - e["data"]).days > 5:
                    continue
                prev, o = float(df["Close"].iloc[pos - 1]), float(df["Open"].iloc[pos])
                h, low, c = float(df["High"].iloc[pos]), float(df["Low"].iloc[pos]), float(df["Close"].iloc[pos])
                if min(prev, o, h, low, c) <= 0:
                    continue
                out.append({**e, "dia_reacao": days[pos],
                            "gap_pct": (o / prev - 1) * 100, "ab_fecho_pct": (c / o - 1) * 100,
                            "ab_max_pct": (h / o - 1) * 100, "ab_min_pct": (low / o - 1) * 100})
        print(f"preços: {min(i + batch, len(syms))}/{len(syms)} ações", flush=True)
    return out


def stats(df: pd.DataFrame) -> dict:
    """Resumo de um conjunto de relatórios, do ponto de vista de quem compra na abertura."""
    if df.empty:
        return {"n": 0}
    oc, hi, lo = df["ab_fecho_pct"], df["ab_max_pct"], df["ab_min_pct"]
    target = np.where(hi >= 5, 5.0, oc)                       # vende a +5% ou, se não chegar, no fecho
    target_stop = np.where(lo <= -3, -3.0, target)            # igual, mas sai a -3% (pessimista: o stop conta primeiro)
    r = lambda v: round(float(v), 2)
    return {"n": int(len(df)), "media_ab_fecho": r(oc.mean()), "mediana_ab_fecho": r(oc.median()),
            "pct_fecho_acima": r((oc > 0).mean() * 100), "pct_max_5": r((hi >= 5).mean() * 100),
            "pct_max_10": r((hi >= 10).mean() * 100), "media_min": r(lo.mean()),
            "alvo5": r(target.mean()), "alvo5_stop3": r(target_stop.mean())}


def select(df: pd.DataFrame, surprise, gap) -> pd.DataFrame:
    if surprise is not None:
        df = df[df["surpresa_pct"] >= surprise]
    if gap is not None:
        df = df[df["gap_pct"] >= gap]
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0, help="limita o número de ações (para testar)")
    ap.add_argument("--cap-min", type=float, default=2e9)
    ap.add_argument("--anos", type=float, default=2)
    ap.add_argument("--surpresa", type=float, default=5, help="critério do bot: surpresa mínima")
    ap.add_argument("--gap", type=float, default=2, help="critério do bot: subida mínima")
    a = ap.parse_args()
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")

    since = date.today() - timedelta(days=int(365 * a.anos))
    uni = universe(a.cap_min)
    syms = sorted(uni, key=lambda s: -uni[s][1])
    if a.max:
        syms = syms[:a.max]
    print(f"universo: {len(syms)} ações com capitalização >= {a.cap_min:,.0f} USD", flush=True)

    events, failed = [], 0
    for i, s in enumerate(syms, 1):
        evs = earnings(s, since)
        if evs is None:
            failed += 1
        else:
            for e in evs:
                e["mercado"], e["capitalizacao"] = uni[s]
            events += evs
        if i % 100 == 0:
            print(f"relatórios: {i}/{len(syms)} ações, {len(events)} relatórios, {failed} falhas", flush=True)
        if failed > 50 and failed > i * 0.3:
            sys.exit("Demasiadas falhas a pedir dados ao Yahoo; a desistir sem gravar.")
        time.sleep(0.15)

    df = pd.DataFrame(reactions(events))
    if df.empty:
        sys.exit("Sem dados.")
    # resultados absurdos (erros de dados, estimativas perto de zero) distorcem as médias
    df = df[(df["gap_pct"].abs() < 80) & (df["ab_fecho_pct"].abs() < 80)]

    bot = select(df, a.surpresa, a.gap)
    bins = [(2e9, 10e9, "2 a 10 mil M$"), (10e9, 50e9, "10 a 50 mil M$"), (50e9, float("inf"), "mais de 50 mil M$")]
    summary = {
        "gerado_em": datetime.now().astimezone().isoformat(timespec="minutes"),
        "periodo": [since.isoformat(), date.today().isoformat()],
        "acoes": int(df["simbolo"].nunique()), "relatorios": int(len(df)), "falhas": failed,
        "criterios_bot": {"surpresa_min": a.surpresa, "gap_min": a.gap},
        "grelha": [{"surpresa_min": s, "gap_min": g, **stats(select(df, s, g))} for s in SURPRISES for g in GAPS],
        "bot": stats(bot),
        "bateu_mas_abriu_a_cair": stats(df[(df["surpresa_pct"] >= a.surpresa) & (df["gap_pct"] < 0)]),
        "por_capitalizacao": [{"grupo": n, **stats(bot[(bot["capitalizacao"] >= lo) & (bot["capitalizacao"] < hi)])}
                              for lo, hi, n in bins],
        "por_momento": [{"grupo": n, **stats(bot[bot["momento"] == m])}
                        for m, n in (("BMO", "antes da abertura"), ("AMC", "depois do fecho"))],
        "por_semestre": [{"grupo": str(p), **stats(g)} for p, g in
                         bot.groupby(pd.PeriodIndex(pd.to_datetime(bot["dia_reacao"]), freq="2Q"))],
    }
    DATA.mkdir(exist_ok=True)
    (DATA / "historico_resumo.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    cols = ["simbolo", "mercado", "capitalizacao", "data", "momento", "eps_estimado", "eps_real", "surpresa_pct",
            "dia_reacao", "gap_pct", "ab_fecho_pct", "ab_max_pct", "ab_min_pct"]
    df.sort_values(["dia_reacao", "simbolo"])[cols].round(3).to_csv(
        DATA / "historico.csv", sep=";", index=False, encoding="utf-8-sig")
    print(json.dumps({k: summary[k] for k in ("acoes", "relatorios", "falhas", "bot", "bateu_mas_abriu_a_cair")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
