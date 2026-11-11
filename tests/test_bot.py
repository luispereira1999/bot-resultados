"""Testes à lógica do bot que não dependem da internet.  Correr:  python -m pytest"""
import csv
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backtest  # noqa: E402
import bot  # noqa: E402


def ts(y, m, d, h, mi=0):
    """Hora de Nova Iorque -> segundos (como o Yahoo dá em regularMarketTime)."""
    return datetime(y, m, d, h, mi, tzinfo=bot.NY).timestamp()


def event(y, m, d, h, mi=0):
    return {"quando": datetime(y, m, d, h, mi, tzinfo=bot.NY).astimezone(bot.LIS).isoformat()}


def info(state, last, pre=None, post=None, reg=None):
    return {"marketState": state, "regularMarketTime": last, "exchangeTimezoneName": "America/New_York",
            "preMarketChangePercent": pre, "postMarketChangePercent": post, "regularMarketChangePercent": reg}


# 2026-11-10 é uma terça-feira

def test_depois_do_fecho_usa_after_hours_e_ignora_a_sessao_anterior():
    ev = event(2026, 11, 10, 16, 5)
    assert bot.price_move(info("POST", ts(2026, 11, 10, 16), post=7.9, reg=-1.0), ev) == (7.9, "after-hours")


def test_depois_do_fecho_sem_negocios_ainda_espera():
    ev = event(2026, 11, 10, 16, 5)
    assert bot.price_move(info("POST", ts(2026, 11, 10, 16), post=None, reg=3.0), ev)[0] is None


def test_depois_do_fecho_na_manha_seguinte_usa_pre_market():
    ev = event(2026, 11, 10, 16, 5)
    assert bot.price_move(info("PRE", ts(2026, 11, 10, 16), pre=4.2, reg=-1.0), ev) == (4.2, "pre-market")


def test_antes_da_abertura_sem_pre_market_espera_em_vez_de_usar_a_vespera():
    ev = event(2026, 11, 10, 7, 0)
    assert bot.price_move(info("PREPRE", ts(2026, 11, 9, 16), pre=None, reg=5.0), ev)[0] is None


def test_antes_da_abertura_com_a_sessao_a_decorrer_usa_a_sessao():
    ev = event(2026, 11, 10, 7, 0)
    assert bot.price_move(info("REGULAR", ts(2026, 11, 10, 11), reg=3.3), ev) == (3.3, "sessão")


def test_sessao_de_reacao_ja_fechada_e_tardio():
    ev = event(2026, 11, 10, 7, 0)
    assert bot.price_move(info("POST", ts(2026, 11, 10, 16), post=1.0, reg=3.3), ev) == (3.3, "tardio")
    assert bot.price_move(info("REGULAR", ts(2026, 11, 11, 11), reg=0.5), ev) == (0.5, "tardio")


def test_sexta_depois_do_fecho_reage_na_segunda():
    ev = event(2026, 11, 13, 16, 30)  # sexta-feira
    assert bot.price_move(info("CLOSED", ts(2026, 11, 13, 16), post=2.5), ev) == (2.5, "after-hours")
    assert bot.price_move(info("REGULAR", ts(2026, 11, 16, 10), reg=6.0), ev) == (6.0, "sessão")
    assert bot.price_move(info("REGULAR", ts(2026, 11, 17, 10), reg=1.0), ev)[1] == "tardio"


def test_sem_preco_nenhum():
    assert bot.price_move({"marketState": "REGULAR"}, event(2026, 11, 10, 7))[0] is None


class FakeTicker:
    def __init__(self, info_):
        self.info = info_

    def __getattr__(self, name):  # estimativas, revisões, etc.: sem dados
        return None


@pytest.mark.parametrize("surprise,move,positive,beat_but_down", [
    (18.0, 7.9, True, False),    # bateu e sobe
    (18.0, 0.5, False, True),    # bateu mas não sobe o suficiente
    (18.0, -6.0, False, True),   # bateu mas cai
    (2.0, 9.0, False, False),    # sobe mas a surpresa é pequena
    (-10.0, 9.0, False, False),  # falhou as estimativas
])
def test_criterios_do_alerta(monkeypatch, surprise, move, positive, beat_but_down):
    ev = {**event(2026, 11, 10, 16, 5), "simbolo": "XYZ", "nome": "XYZ", "mercado": "NASDAQ", "dia": "2026-11-10",
          "momento": "AMC", "eps_estimado": 1.0, "eps_real": 1.2, "surpresa_pct": surprise}
    monkeypatch.setattr(bot.yf, "Ticker", lambda s: FakeTicker(info("POST", ts(2026, 11, 10, 16), post=move)))
    r = bot.evaluate(dict(bot.DEFAULTS), ev)
    assert (r["positivo"], r["bateu_mas_cai"]) == (positive, beat_but_down)


def test_resultado_tardio_nunca_gera_alerta(monkeypatch):
    ev = {**event(2026, 11, 10, 7), "simbolo": "XYZ", "nome": "XYZ", "mercado": "NYSE", "dia": "2026-11-10",
          "momento": "BMO", "eps_estimado": 1.0, "eps_real": 2.0, "surpresa_pct": 100.0}
    monkeypatch.setattr(bot.yf, "Ticker", lambda s: FakeTicker(info("REGULAR", ts(2026, 11, 12, 11), reg=9.0)))
    r = bot.evaluate(dict(bot.DEFAULTS), ev)
    assert r["movimento_tipo"] == "tardio" and not r["positivo"] and not r["bateu_mas_cai"]


def test_sem_preco_fica_para_a_proxima_passagem(monkeypatch):
    ev = {**event(2026, 11, 10, 16, 5), "simbolo": "XYZ", "nome": "XYZ", "mercado": "NYSE", "dia": "2026-11-10",
          "momento": "AMC", "eps_estimado": 1.0, "eps_real": 2.0, "surpresa_pct": 100.0}
    monkeypatch.setattr(bot.yf, "Ticker", lambda s: FakeTicker(info("POST", ts(2026, 11, 10, 16))))
    assert bot.evaluate(dict(bot.DEFAULTS), ev) is None


def test_registo_antigo_ganha_as_colunas_novas(tmp_path, monkeypatch):
    log = tmp_path / "registo.csv"
    old_cols = [c for c in bot.LOG_COLUMNS if c != "atraso_min"]
    with log.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=old_cols, delimiter=";")
        w.writeheader()
        w.writerow({"simbolo": "ANTIGA", "nome": "Empresa; com ponto e vírgula"})
    monkeypatch.setattr(bot, "LOG_FILE", log)
    monkeypatch.setattr(bot, "DATA", tmp_path)
    bot.append_log({"simbolo": "NOVA", "atraso_min": 7})
    with log.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    assert list(rows[0].keys()) == bot.LOG_COLUMNS
    assert [(r["simbolo"], r["atraso_min"]) for r in rows] == [("ANTIGA", ""), ("NOVA", "7")]
    assert rows[0]["nome"] == "Empresa; com ponto e vírgula"


def test_num_e_formatos():
    assert bot.num("1.5") == 1.5 and bot.num(float("nan")) is None and bot.num(None) is None and bot.num("x") is None
    assert bot.fmt_pct(7.94) == "+7.9%" and bot.fmt_pct(None) == "?"
    assert bot.fmt_big(5.3e9) == "5.3 mil M"


def test_abertura_de_nova_iorque_em_hora_de_lisboa():
    assert bot.ny_open(datetime(2026, 11, 11, 10, 0, tzinfo=bot.LIS)).astimezone(bot.LIS).strftime("%H:%M") == "14:30"
    # 28/10/2026: a Europa já mudou a hora e os EUA ainda não -> abre às 13:30 de Lisboa
    assert bot.ny_open(datetime(2026, 10, 28, 10, 0, tzinfo=bot.LIS)).astimezone(bot.LIS).strftime("%H:%M") == "13:30"


def test_teste_historico_resumo_e_filtros():
    df = pd.DataFrame({"surpresa_pct": [10, 10, 1, 30], "gap_pct": [3, -4, 5, 6],
                       "ab_fecho_pct": [2.0, -1.0, 4.0, -6.0], "ab_max_pct": [6.0, 0.5, 4.5, 1.0],
                       "ab_min_pct": [-1.0, -2.0, -0.5, -7.0]})
    sel = backtest.select(df, 5, 2)
    assert len(sel) == 2
    s = backtest.stats(sel)
    assert s["n"] == 2 and s["media_ab_fecho"] == -2.0 and s["pct_fecho_acima"] == 50.0 and s["pct_max_5"] == 50.0
    assert s["alvo5"] == -0.5          # um vende a +5, o outro fecha a -6
    assert s["alvo5_stop3"] == 1.0     # o segundo sai a -3
    assert backtest.stats(df.iloc[0:0]) == {"n": 0}
    assert len(backtest.select(df, None, None)) == 4


def test_guidance_escolhe_frases_de_previsao_e_ignora_texto_juridico():
    import guidance
    page = """<html><body><p>Revenue was $1.2 billion in the third quarter of 2026, up 12% year over year.</p>
    <p>For the fourth quarter of 2026, the company expects revenue of $1.30 billion to $1.35 billion.</p>
    <p>The company raised its full-year 2026 guidance for adjusted EPS to $4.10&nbsp;to $4.20 from $3.90 to $4.00.</p>
    <p>This release contains forward-looking statements; the company expects actual results may differ by 10% in 2026.</p>
    <p>We expect great things.</p></body></html>"""
    frases = guidance.extract(guidance.to_text(page))
    assert len(frases) == 2
    assert frases[0].startswith("For the fourth quarter of 2026") and "raised its full-year 2026 guidance" in frases[1]
    assert "$4.10 to $4.20" in frases[1]


def test_alerta_inclui_o_guidance_quando_existe():
    r = {"simbolo": "XYZ", "nome": "XYZ Inc", "mercado": "NASDAQ", "momento": "AMC", "positivo": True,
         "surpresa_pct": 10.0, "movimento_pct": 5.0, "movimento_tipo": "after-hours", "eps_real": 1.1, "eps_estimado": 1.0}
    _, body = bot.alert_text(r, {"frases": ["Expects 2027 revenue of $5 billion."], "url": "https://x"})
    assert "• Expects 2027 revenue of $5 billion." in body
    assert "lê a secção" in bot.alert_text(r)[1]
    assert bot.links(r, "https://x")[1][0]["url"] == "https://x"
