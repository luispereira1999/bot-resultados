"""Teste do pre-market: comprar antes da abertura, quando a ação já está a reagir ao relatório.

O teste histórico (backtest.py) só tem preços diários e compra na abertura. Este usa barras de 1 hora com
pre-market e after-hours do Yahoo (cerca de 2 anos) para os relatórios de data/historico.csv e mede três
momentos de compra:
  ah17  after-hours às 17:00 de Nova Iorque, só relatórios depois do fecho (quando chega o alerta da SEC)
  pm08  pre-market às 08:00 do dia da reação
  pm09  pre-market às 09:00 do dia da reação (meia hora antes da abertura, hora do resumo pre-market)
e duas saídas: a abertura (09:30) e o fecho (16:00) do dia da reação.

"Movimento" = preço no momento da compra face ao fecho anterior (o que o bot vê quando avisa).

Limitações: o Yahoo não dá volume fora da sessão, por isso não se sabe se havia liquidez para comprar a esse
preço; o preço é o da abertura da barra de 1 hora; a diferença entre compra e venda no pre-market costuma
ser maior do que na sessão e não está descontada.

Uso:  python premarket.py [--max N]      descarrega as barras e grava data/premarket.csv e o resumo
      python premarket.py --so-resumo    recalcula o resumo a partir de data/premarket.csv
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
NY = "America/New_York"
ENTRIES = {"ah17": "after-hours 17:00 (só depois do fecho)", "pm08": "pre-market 08:00", "pm09": "pre-market 09:00"}
SURPRISES = [None, 0, 5, 10, 20]
MOVES = [None, 0, 1, 2, 3, 5]          # movimento mínimo no momento da compra
MOVE_MAX = [None, 10]
CAPS = [None, 10e9, 50e9]


def hhmm(ts: pd.Timestamp) -> str:
    return ts.strftime("%H:%M")


def measure(bars: pd.DataFrame, events: list[dict]) -> list[dict]:
    """Preços de cada relatório nas barras de 1 hora de uma ação (índice em hora de Nova Iorque)."""
    bars = bars.dropna(subset=["Open", "Close"])
    if bars.empty:
        return []
    by_day = {d: g for d, g in bars.groupby(bars.index.date)}
    days = sorted(by_day)
    out = []
    for e in events:
        rd = pd.Timestamp(e["dia_reacao"]).date()
        if rd not in by_day:
            continue
        today = by_day[rd]
        t = today.index.map(hhmm)
        reg = today[(t >= "09:30") & (t < "16:00")]
        prev_days = [d for d in days if d < rd]
        if reg.empty or hhmm(reg.index[0]) != "09:30" or not prev_days:
            continue
        prev = by_day[prev_days[-1]]
        pt = prev.index.map(hhmm)
        prev_reg = prev[(pt >= "09:30") & (pt < "16:00")]
        if prev_reg.empty:
            continue

        def at(frame: pd.DataFrame, times, when: str):
            hit = frame[times == when]
            return float(hit["Open"].iloc[0]) if len(hit) else None

        row = {**e, "fecho_anterior": float(prev_reg["Close"].iloc[-1]), "abertura": float(reg["Open"].iloc[0]),
               "fecho": float(reg["Close"].iloc[-1]), "maximo": float(reg["High"].max()), "minimo": float(reg["Low"].min()),
               "pm08": at(today, t, "08:00"), "pm09": at(today, t, "09:00"), "ah17": None}
        report_day = pd.Timestamp(e["data"]).date()
        if e["momento"] == "AMC" and report_day == prev_days[-1]:
            row["ah17"] = at(prev, pt, "17:00")
        out.append(row)
    return out


def download(events: pd.DataFrame, batch: int = 80) -> pd.DataFrame:
    by_sym: dict[str, list[dict]] = {}
    for e in events.to_dict("records"):
        by_sym.setdefault(e["simbolo"], []).append(e)
    syms, rows = sorted(by_sym), []
    for i in range(0, len(syms), batch):
        chunk = syms[i:i + batch]
        px = None
        for attempt in range(3):
            try:
                px = yf.download(chunk, period="730d", interval="1h", prepost=True, auto_adjust=True,
                                 group_by="ticker", threads=True, progress=False)
                break
            except Exception as exc:
                print(f"barras: lote {i} falhou ({exc}), nova tentativa", file=sys.stderr)
                time.sleep(20 * (attempt + 1))
        if px is None or px.empty:
            continue
        for s in chunk:
            try:
                b = px[s] if isinstance(px.columns, pd.MultiIndex) else px
            except KeyError:
                continue
            b = b.copy()
            b.index = b.index.tz_convert(NY) if b.index.tz is not None else b.index.tz_localize("UTC").tz_convert(NY)
            rows += measure(b, by_sym[s])
        print(f"barras: {min(i + batch, len(syms))}/{len(syms)} ações, {len(rows)} relatórios medidos", flush=True)
    return pd.DataFrame(rows)


def returns(df: pd.DataFrame, entry: str) -> pd.DataFrame:
    """Para um momento de compra: movimento até lá, e ganho até à abertura, ao fecho e ao máximo."""
    d = df[df[entry].notna() & (df[entry] > 0) & (df["fecho_anterior"] > 0)].copy()
    d["mov"] = (d[entry] / d["fecho_anterior"] - 1) * 100
    d["ate_abertura"] = (d["abertura"] / d[entry] - 1) * 100
    d["ate_fecho"] = (d["fecho"] / d[entry] - 1) * 100
    d["ate_maximo"] = (d["maximo"] / d[entry] - 1) * 100
    d["ate_minimo"] = (d["minimo"] / d[entry] - 1) * 100
    ok = (d["mov"].abs() < 80) & (d["ate_fecho"].abs() < 80) & (d["ate_abertura"].abs() < 80)
    return d[ok]


def select(d: pd.DataFrame, s=None, m=None, mmax=None, cap=None) -> pd.DataFrame:
    if s is not None:
        d = d[d["surpresa_pct"] >= s]
    if m is not None:
        d = d[d["mov"] >= m]
    if mmax is not None:
        d = d[d["mov"] < mmax]
    if cap is not None:
        d = d[d["capitalizacao"] >= cap]
    return d


def stats(d: pd.DataFrame) -> dict:
    if d.empty:
        return {"n": 0}
    r = lambda v: round(float(v), 2)
    a, f = d["ate_abertura"], d["ate_fecho"]
    stop = np.where(d["ate_minimo"] <= -3, -3.0, np.where(d["ate_maximo"] >= 5, 5.0, f))  # pessimista: o stop conta primeiro
    return {"n": int(len(d)), "media_abertura": r(a.mean()), "mediana_abertura": r(a.median()),
            "pct_sobe_ate_abertura": r((a > 0).mean() * 100), "media_fecho": r(f.mean()), "mediana_fecho": r(f.median()),
            "pct_sobe_ate_fecho": r((f > 0).mean() * 100), "alvo5_stop3": r(stop.mean()),
            "t_fecho": r(f.mean() / (f.std() / np.sqrt(len(f)))) if len(f) > 2 and f.std() > 0 else None}


def rule(s, m, mmax, cap) -> str:
    parts = [f"surpresa ≥ {s:g}%" if s is not None else "qualquer surpresa"]
    if m is not None and mmax is not None:
        parts.append(f"movimento entre {m:+g}% e {mmax:+g}%")
    elif m is not None:
        parts.append(f"movimento ≥ {m:+g}%")
    elif mmax is not None:
        parts.append(f"movimento < {mmax:+g}%")
    if cap:
        parts.append(f"capitalização ≥ {cap / 1e9:g} mil M$")
    return ", ".join(parts)


def bot_rule() -> dict:
    """O critério do bot (config.json), traduzido para este teste: o movimento é o do momento da compra."""
    import backtest
    c = backtest.bot_criterion(ROOT / "config.json")
    return {"s": c["surpresa_min"], "m": c["gap_min"], "mmax": c["gap_max"], "cap": c["cap_min"]}


def summary(df: pd.DataFrame, min_n: int = 100) -> dict:
    b = bot_rule()
    out = {"gerado_em": datetime.now().astimezone().isoformat(timespec="minutes"),
           "relatorios": int(len(df)), "periodo": [str(df["dia_reacao"].min()), str(df["dia_reacao"].max())],
           "criterio_bot": rule(b["s"], b["m"], b["mmax"], b["cap"]), "entradas": {}}
    for entry, label in ENTRIES.items():
        d = returns(df, entry)
        if d.empty:
            continue
        bot_sel = select(d, b["s"], b["m"], b["mmax"], b["cap"])
        q_all = d.groupby(pd.PeriodIndex(pd.to_datetime(d["dia_reacao"]), freq="Q"))["ate_fecho"].mean()
        q_bot = bot_sel.groupby(pd.PeriodIndex(pd.to_datetime(bot_sel["dia_reacao"]), freq="Q"))["ate_fecho"].mean()
        days = pd.to_datetime(d["dia_reacao"])
        cut = days.min() + (days.max() - days.min()) / 2
        halves = {1: d[days < cut], 2: d[days >= cut]}
        grid = []
        for s in SURPRISES:
            for m in MOVES:
                for mmax in MOVE_MAX:
                    if m is not None and mmax is not None and mmax <= m:
                        continue
                    for cap in CAPS:
                        sel = select(d, s, m, mmax, cap)
                        g = {"regra": rule(s, m, mmax, cap), "surpresa_min": s, "mov_min": m, "mov_max": mmax, "cap_min": cap,
                             **stats(sel)}
                        for h, hd in halves.items():
                            hs = select(hd, s, m, mmax, cap)["ate_fecho"]
                            g[f"n{h}"] = int(len(hs))
                            g[f"media_fecho{h}"] = round(float(hs.mean()), 2) if len(hs) else None
                        grid.append(g)
        valid = [g for g in grid if g["n1"] >= min_n and g["n2"] >= min_n]
        out["entradas"][entry] = {
            "nome": label, "n": int(len(d)), "corte": cut.date().isoformat(), "todos": stats(d),
            "bot": {**stats(bot_sel), "trimestres_acima": int(sum((q_bot - q_all).dropna() > 0)), "trimestres": int(q_bot.size)},
            "grelha": grid,
            # escolhido numa metade, medido na outra (pelo ganho até ao fecho)
            "escolhidos_na_1": [{k: g[k] for k in ("regra", "n1", "media_fecho1", "n2", "media_fecho2")}
                                for g in sorted(valid, key=lambda g: -g["media_fecho1"])[:5]],
            "escolhidos_na_2": [{k: g[k] for k in ("regra", "n2", "media_fecho2", "n1", "media_fecho1")}
                                for g in sorted(valid, key=lambda g: -g["media_fecho2"])[:5]],
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0, help="limita o número de ações (para testar)")
    ap.add_argument("--so-resumo", action="store_true")
    a = ap.parse_args()
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    if a.so_resumo:
        df = pd.read_csv(DATA / "premarket.csv", sep=";", encoding="utf-8-sig")
    else:
        ev = pd.read_csv(DATA / "historico.csv", sep=";", encoding="utf-8-sig")
        if a.max:
            keep = ev.groupby("simbolo")["capitalizacao"].max().sort_values(ascending=False).index[:a.max]
            ev = ev[ev["simbolo"].isin(keep)]
        cols = ["simbolo", "capitalizacao", "data", "momento", "eps_estimado", "eps_real", "surpresa_pct", "dia_reacao"]
        df = download(ev[cols])
        if df.empty:
            sys.exit("Sem dados.")
        df.round(4).to_csv(DATA / "premarket.csv", sep=";", index=False, encoding="utf-8-sig")
    s = summary(df)
    (DATA / "premarket_resumo.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"critério do bot: {s['criterio_bot']}")
    for entry, e in s["entradas"].items():
        t, b = e["todos"], e["bot"]
        print(f"{e['nome']}: todos n={e['n']} até ao fecho {t['media_fecho']:+.2f}% | critério do bot n={b['n']}"
              + (f", até à abertura {b['media_abertura']:+.2f}%, até ao fecho {b['media_fecho']:+.2f}%, "
                 f"acima da média em {b['trimestres_acima']}/{b['trimestres']} trimestres" if b["n"] else ""))


if __name__ == "__main__":
    main()
