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


# ---------------------------------------------------------------- deteção rápida pela SEC

FEED = """<?xml version="1.0" encoding="ISO-8859-1" ?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>8-K - XYZ CORP (0000001234) (Filer)</title>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-11-10 &lt;b&gt;AccNo:&lt;/b&gt; 0000001234-26-000001
&lt;br&gt;Item 2.02: Results of Operations and Financial Condition
&lt;br&gt;Item 9.01: Financial Statements and Exhibits</summary>
<updated>2026-11-10T16:06:00-05:00</updated><category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
<id>urn:tag:sec.gov,2008:accession-number=0000001234-26-000001</id></entry>
<entry><title>8-K - OUTRA INC (0000005678) (Filer)</title>
<summary type="html">&lt;br&gt;Item 5.02: Departure of Directors</summary>
<updated>2026-11-10T16:04:00-05:00</updated><category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
<id>urn:tag:sec.gov,2008:accession-number=0000005678-26-000009</id></entry></feed>"""


def test_feed_da_sec_le_cik_items_e_hora():
    import guidance
    e = guidance.parse_feed(FEED)
    assert [x["cik"] for x in e] == [1234, 5678]
    assert e[0]["items"] == ["2.02", "9.01"] and e[0]["accession"] == "0000001234-26-000001"
    assert e[0]["entregue"].astimezone(bot.LIS).strftime("%d %H:%M") == "10 21:06" and e[0]["nome"] == "XYZ CORP"


@pytest.mark.parametrize("texto,eps,ajustado", [
    ("Third quarter 2026 adjusted diluted EPS of $1.32, up 10%. GAAP diluted EPS of $1.05.", 1.32, True),
    ("Net income for the third quarter was $50.1 million, or $0.45 per diluted share.", 0.45, False),
    ("Net loss for the third quarter of 2026 was $66.2 million, or $1.05 per share.", -1.05, False),
    ("Non-GAAP diluted earnings per ADS was RMB8.10 (US$1.21) in the quarter.", 8.10, True),
    ("Diluted EPS increased 21% to $2.38 in the third quarter.", 2.38, False),
    ("Net loss per share attributable to common stockholders - diluted $ (0.15)", -0.15, False),
])
def test_eps_do_comunicado(texto, eps, ajustado):
    import guidance
    r = guidance.eps_from_release(texto)
    assert r is not None and r["eps"] == pytest.approx(eps) and r["ajustado"] == ajustado


@pytest.mark.parametrize("texto", [
    "The Board declared a quarterly dividend of $0.25 per share.",                        # dividendo
    "Shares were repurchased at an average price of $42.64 per share.",                   # preço
    "The company expects full-year adjusted EPS of $4.10 to $4.20.",                      # previsão
    "Adjusted EPS of $3.44.\nAdjusted EPS of $0.91.",                                     # trimestre e ano sem distinção
    "Revenue grew 12% to $1.2 billion.",                                                  # sem EPS
    "Net income was $10 million, compared to a net loss of $2 million in 2025, or $(0.02) per share.",  # ano anterior
])
def test_eps_do_comunicado_abstem_se_quando_nao_ha_valor_claro(texto):
    import guidance
    assert guidance.eps_from_release(texto) is None


@pytest.fixture
def sec_env(monkeypatch, tmp_path):
    """Bot com um relatório pendente da XYZ (depois do fecho de 10/11), SEC e Yahoo simulados."""
    import guidance
    sent = []
    cfg = {**bot.DEFAULTS, "sec_contacto": "x", "guidance_no_alerta": True}
    ev = {**event(2026, 11, 10, 16, 5), "simbolo": "XYZ", "nome": "XYZ", "mercado": "NASDAQ", "capitalizacao": 5e9,
          "dia": "2026-11-10", "momento": "AMC", "eps_estimado": 1.0, "eps_real": None, "surpresa_pct": None}
    monkeypatch.setattr(bot, "SEC_MARK", tmp_path / "sec_ultimo.txt")
    monkeypatch.setattr(guidance, "cik_for", lambda s, ua: 1234 if s == "XYZ" else None)
    monkeypatch.setattr(guidance, "recent_results", lambda ua, since: guidance.parse_feed(FEED))
    release = {"texto": "Third quarter adjusted diluted EPS of $1.20. The company expects fiscal 2027 revenue of $5 billion."}
    monkeypatch.setattr(guidance, "release_text", lambda cik, acc, ua: ("https://sec/x", release["texto"], release.get("html", "")))
    move = {"post": 6.0}
    monkeypatch.setattr(bot.yf, "Ticker", lambda s: FakeTicker(info("POST", ts(2026, 11, 10, 16), post=move["post"])))
    monkeypatch.setattr(bot, "notify", lambda cfg, title, body, **kw: sent.append((title, body)))
    monkeypatch.setattr(bot, "append_log", lambda row: None)
    monkeypatch.setattr(bot, "refresh_eu", lambda *a: [])
    monkeypatch.setattr(bot, "listed_symbols", lambda d: {})
    yahoo = {"eventos": [ev]}
    monkeypatch.setattr(bot, "us_events", lambda *a: [dict(e) for e in yahoo["eventos"]])
    now = datetime(2026, 11, 10, 21, 10, tzinfo=bot.LIS)
    return {"cfg": cfg, "ev": ev, "sent": sent, "move": move, "yahoo": yahoo, "release": release, "now": now}


def test_alerta_antecipado_quando_o_comunicado_bate_e_o_preco_sobe(sec_env):
    state = {}
    bot.task_check(sec_env["cfg"], state, sec_env["now"], dry=False)
    assert len(sec_env["sent"]) == 1
    title, body = sec_env["sent"][0]
    assert title.startswith("🟢 XYZ bateu +20.0%") and title.endswith("⚡")
    assert "Antecipado" in body and "ajustado" in body and "expects fiscal 2027 revenue" in body
    assert state["sec"]["XYZ|2026-11-10"]["estado"] == "alertado" and "XYZ|2026-11-10" in state["positivos"]


def test_antecipado_espera_que_o_preco_suba(sec_env):
    state = {}
    sec_env["move"]["post"] = 0.5
    bot.task_check(sec_env["cfg"], state, sec_env["now"], dry=False)
    assert sec_env["sent"] == [] and state["sec"]["XYZ|2026-11-10"]["estado"] == "a seguir"
    sec_env["move"]["post"] = 4.0  # na passagem seguinte o preço já sobe
    bot.task_check(sec_env["cfg"], state, sec_env["now"] + pd.Timedelta(minutes=2), dry=False)
    assert len(sec_env["sent"]) == 1 and state["sec"]["XYZ|2026-11-10"]["estado"] == "alertado"


def test_yahoo_confirma_sem_repetir_o_alerta(sec_env):
    state = {}
    bot.task_check(sec_env["cfg"], state, sec_env["now"], dry=False)
    sec_env["yahoo"]["eventos"][0].update(eps_real=1.21, surpresa_pct=21.0)
    bot.task_check(sec_env["cfg"], state, sec_env["now"] + pd.Timedelta(hours=4), dry=False)
    assert len(sec_env["sent"]) == 1 and "XYZ|2026-11-10" in state["vistos"]


def test_yahoo_desmente_e_o_bot_corrige(sec_env):
    state = {}
    bot.task_check(sec_env["cfg"], state, sec_env["now"], dry=False)
    sec_env["yahoo"]["eventos"][0].update(eps_real=0.98, surpresa_pct=-2.0)
    bot.task_check(sec_env["cfg"], state, sec_env["now"] + pd.Timedelta(hours=4), dry=False)
    assert len(sec_env["sent"]) == 2 and "não se confirma" in sec_env["sent"][1][0]
    assert "XYZ|2026-11-10" not in state["positivos"]


def test_comunicado_sem_eps_claro_espera_pelo_yahoo(sec_env):
    state = {}
    sec_env["release"]["texto"] = "Revenue grew 12% to $1.2 billion."
    bot.task_check(sec_env["cfg"], state, sec_env["now"], dry=False)
    assert sec_env["sent"] == [] and state["sec"]["XYZ|2026-11-10"]["estado"] == "sem EPS"
    sec_env["yahoo"]["eventos"][0].update(eps_real=1.2, surpresa_pct=20.0)
    bot.task_check(sec_env["cfg"], state, sec_env["now"] + pd.Timedelta(hours=4), dry=False)
    assert len(sec_env["sent"]) == 1 and "Antecipado" not in sec_env["sent"][0][1]


def test_falha_na_sec_nao_impede_o_yahoo(sec_env, monkeypatch):
    import guidance
    def boom(*a):
        raise ConnectionError("SEC em baixo")
    monkeypatch.setattr(guidance, "recent_results", boom)
    monkeypatch.setattr(bot, "log_error", lambda msg: None)
    monkeypatch.setattr(bot, "fetch_guidance", lambda cfg, r: None)
    sec_env["yahoo"]["eventos"][0].update(eps_real=1.2, surpresa_pct=20.0)
    bot.task_check(sec_env["cfg"], {}, sec_env["now"], dry=False)
    assert len(sec_env["sent"]) == 1


def test_comunicado_que_falha_nao_bloqueia_as_passagens_seguintes(sec_env, monkeypatch):
    import guidance
    calls = []
    def broken(cik, acc, ua):
        calls.append(acc)
        raise ValueError("comunicado sem documentos html")
    monkeypatch.setattr(guidance, "release_text", broken)
    monkeypatch.setattr(bot, "log_error", lambda msg: None)
    state = {}
    bot.task_check(sec_env["cfg"], state, sec_env["now"], dry=False)
    bot.task_check(sec_env["cfg"], state, sec_env["now"] + pd.Timedelta(minutes=2), dry=False)
    assert len(calls) == 1 and state["sec"]["XYZ|2026-11-10"]["estado"] == "erro"


# ---------------------------------------------------------------- critério do alerta e validação

@pytest.mark.parametrize("move,cap,ok", [
    (-3.0, 60e9, True),    # abriu a cair um pouco, mas dentro do intervalo
    (4.9, 60e9, True),
    (5.0, 60e9, False),    # acima do máximo
    (-6.0, 60e9, False),   # abaixo do mínimo
    (1.0, 20e9, False),    # empresa pequena demais
    (1.0, None, False),    # sem capitalização conhecida
])
def test_criterio_com_intervalo_de_preco_e_capitalizacao(move, cap, ok):
    cfg = {**bot.DEFAULTS, "movimento_preco_minimo_pct": -5, "movimento_preco_maximo_pct": 5,
           "capitalizacao_minima_alerta_usd": 50e9}
    assert (bot.price_ok(cfg, move) and bot.cap_ok(cfg, cap)) == ok


def test_criterio_por_omissao_nao_muda():
    cfg = dict(bot.DEFAULTS)
    assert bot.price_ok(cfg, 40.0) and not bot.price_ok(cfg, 1.9) and bot.cap_ok(cfg, None)


def test_teste_historico_le_o_criterio_do_config(tmp_path):
    p = tmp_path / "config.json"
    p.write_text('{"surpresa_eps_minima_pct": 10, "movimento_preco_minimo_pct": -5, "movimento_preco_maximo_pct": 5,'
                 ' "capitalizacao_minima_alerta_usd": 50000000000}', encoding="utf-8")
    c = backtest.bot_criterion(p)
    assert c == {"surpresa_min": 10, "gap_min": -5, "gap_max": 5, "cap_min": 50e9}
    assert backtest.describe(c) == "surpresa ≥ 10%, abertura entre -5% e +5%, capitalização ≥ 50 mil M$"
    assert backtest.bot_criterion(tmp_path / "nao_existe.json")["gap_min"] == 2


def _historico(n_per_quarter=40):
    """Relatórios sintéticos: os de surpresa >= 10 sobem 1 ponto acima dos outros em todos os trimestres."""
    rows = []
    for q, day in enumerate(pd.date_range("2025-01-15", periods=8, freq="QS")):
        for i in range(n_per_quarter):
            big = i % 2 == 0
            rows.append({"simbolo": f"S{i}", "dia_reacao": (day + pd.Timedelta(days=i % 20)).date().isoformat(),
                         "surpresa_pct": 15.0 if big else 1.0, "gap_pct": 1.0, "capitalizacao": 60e9,
                         "ab_fecho_pct": (1.5 if big else 0.5) + (0.2 if i % 4 < 2 else -0.2) + q * 0.1,
                         "ab_max_pct": 2.0, "ab_min_pct": -1.0})
    return pd.DataFrame(rows)


def test_relatorio_do_criterio_compara_com_o_mesmo_trimestre():
    df = _historico()
    r = backtest.criterion_report(df, "x", {"surpresa_min": 10, "gap_min": None, "gap_max": None, "cap_min": None})
    assert r["n"] == 160 and r["trimestres"] == 8 and r["trimestres_acima"] == 8
    assert all(x["excesso"] == pytest.approx(0.5) for x in r["por_trimestre"])  # 1 ponto acima de uma média que já os inclui


def test_validacao_fora_da_amostra_escolhe_numa_metade_e_mede_na_outra():
    v = backtest.out_of_sample(_historico(), min_n=20)
    assert v["combinacoes"] > 0
    for a in (1, 2):
        best = v[f"escolhidos_na_{a}"][0]
        assert "surpresa ≥ 10%" in best["regra"] or "surpresa ≥ 5%" in best["regra"]
        assert best["excesso_teste"] == pytest.approx(0.5)


# ---------------------------------------------------------------- teste do pre-market

def _bars(day_prices: dict) -> pd.DataFrame:
    """Barras de 1 hora em hora de Nova Iorque: {"2026-11-09": {"09:30": 100, ...}} (abertura = fecho = preço)."""
    rows = []
    for day, prices in day_prices.items():
        for hm, p in prices.items():
            rows.append({"ts": pd.Timestamp(f"{day} {hm}", tz="America/New_York"), "Open": p, "High": p + 1, "Low": p - 1, "Close": p})
    return pd.DataFrame(rows).set_index("ts")


def test_premarket_mede_os_precos_de_cada_momento():
    import premarket
    bars = _bars({
        "2026-11-09": {"09:30": 98, "15:30": 100, "17:00": 104},                 # dia do relatório (depois do fecho)
        "2026-11-10": {"08:00": 105, "09:00": 106, "09:30": 107, "12:30": 109, "15:30": 110},  # dia da reação
    })
    ev = {"simbolo": "XYZ", "data": "2026-11-09", "momento": "AMC", "dia_reacao": "2026-11-10", "surpresa_pct": 20.0,
          "capitalizacao": 60e9}
    r = premarket.measure(bars, [ev])[0]
    assert (r["fecho_anterior"], r["ah17"], r["pm08"], r["pm09"], r["abertura"], r["fecho"]) == (100, 104, 105, 106, 107, 110)
    assert r["maximo"] == 111 and r["minimo"] == 106  # só a sessão do dia da reação (107 a 110, ±1)
    d = premarket.returns(pd.DataFrame([r]), "pm09").iloc[0]
    assert d["mov"] == pytest.approx(6.0) and d["ate_abertura"] == pytest.approx(100 * (107 / 106 - 1))
    assert d["ate_fecho"] == pytest.approx(100 * (110 / 106 - 1))


def test_premarket_antes_da_abertura_nao_tem_after_hours():
    import premarket
    bars = _bars({"2026-11-09": {"09:30": 99, "15:30": 100}, "2026-11-10": {"08:00": 103, "09:30": 104, "15:30": 102}})
    ev = {"simbolo": "XYZ", "data": "2026-11-10", "momento": "BMO", "dia_reacao": "2026-11-10", "surpresa_pct": 5.0,
          "capitalizacao": 5e9}
    r = premarket.measure(bars, [ev])[0]
    assert r["ah17"] is None and r["pm09"] is None and r["pm08"] == 103
    assert premarket.returns(pd.DataFrame([r]), "pm09").empty


def test_premarket_sem_sessao_no_dia_da_reacao_fica_de_fora():
    import premarket
    bars = _bars({"2026-11-09": {"09:30": 99, "15:30": 100}})
    ev = {"simbolo": "XYZ", "data": "2026-11-09", "momento": "AMC", "dia_reacao": "2026-11-10", "surpresa_pct": 5.0,
          "capitalizacao": 5e9}
    assert premarket.measure(bars, [ev]) == []


# ---------------------------------------------------------------- avisos de falhas

def test_avisa_ao_fim_de_5_falhas_seguidas_e_quando_recupera(monkeypatch):
    sent = []
    monkeypatch.setattr(bot, "notify", lambda cfg, title, body, **kw: sent.append(title))
    cfg, state = dict(bot.DEFAULTS), {}
    for _ in range(4):
        bot.track_health(cfg, state, "sec", False, "ConnectionError", dry=False)
    assert sent == [] and state["saude"]["sec"]["falhas"] == 4
    bot.track_health(cfg, state, "sec", False, "ConnectionError", dry=False)
    bot.track_health(cfg, state, "sec", False, "ConnectionError", dry=False)  # não repete o aviso
    assert len(sent) == 1 and "SEC" in sent[0] and "erros" in sent[0]
    bot.track_health(cfg, state, "sec", True, "", dry=False)
    assert len(sent) == 2 and "voltou a funcionar" in sent[1] and "saude" not in state


def test_saude_sem_falhas_nao_mexe_no_estado():
    state = {}
    bot.track_health(dict(bot.DEFAULTS), state, "bot", True, "", dry=False)
    assert state == {}  # sem mudanças no estado: sem commits a cada passagem


def test_feed_da_sec_nao_fica_no_estado(sec_env, tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "SEC_MARK", tmp_path / "sec_ultimo.txt")
    state = {"sec_ultimo": "2026-11-10T20:00:00+00:00"}
    bot.task_check(sec_env["cfg"], state, sec_env["now"], dry=False)
    assert "sec_ultimo" not in state and bot.read_sec_mark() == sec_env["now"].isoformat()


def test_vigia_avisa_quando_o_bot_para():
    import vigia
    from datetime import timezone
    now = datetime(2026, 11, 24, 10, 20, tzinfo=timezone.utc)  # terça-feira
    ago = lambda m: (now - pd.Timedelta(minutes=m)).isoformat().replace("+00:00", "Z")
    assert vigia.minutes_idle([{"status": "in_progress", "updatedAt": ago(300)}], now) is None
    idle = vigia.minutes_idle([{"status": "completed", "updatedAt": ago(95)}], now)
    assert round(idle) == 95 and vigia.should_alert(idle, now)
    assert not vigia.should_alert(vigia.minutes_idle([{"status": "completed", "updatedAt": ago(60)}], now), now)
    assert not vigia.should_alert(vigia.minutes_idle([{"status": "completed", "updatedAt": ago(400)}], now), now)  # já avisou
    assert vigia.should_alert(400, datetime(2026, 11, 24, 13, 20, tzinfo=timezone.utc))  # lembrete às 13:00 UTC


def test_vigia_a_segunda_conta_desde_o_arranque_esperado():
    import vigia
    from datetime import timezone
    monday = datetime(2026, 11, 23, 1, 50, tzinfo=timezone.utc)
    friday_run = [{"status": "completed", "updatedAt": "2026-11-21T01:30:00Z"}]  # última corrida no sábado de madrugada
    idle = vigia.minutes_idle(friday_run, monday)
    assert round(idle) == 105 and vigia.should_alert(idle, monday)


TABELA = """<table><tr><td>Three Months Ended September 30</td><td>2026</td><td>2025</td></tr>
<tr><td>Revenue</td><td>$</td><td>1,200</td><td>$</td><td>1,050</td></tr>
<tr><td>Adjusted diluted EPS (1)</td><td>$</td><td>1.32</td><td>$</td><td>1.10</td></tr>
<tr><td>Diluted EPS</td><td>$</td><td>1.05</td><td>$</td><td>0.98</td></tr>
<tr><td>Net loss per share – diluted</td><td>(0.15</td><td>)</td><td>(0.20</td><td>)</td></tr></table>"""


def test_eps_das_tabelas_usa_o_ajustado_do_trimestre():
    import guidance
    r = guidance.eps_from_tables(TABELA)
    assert r["eps"] == 1.32 and r["ajustado"]
    sem_ajustado = TABELA.replace("Adjusted diluted EPS (1)", "Revenue per employee")
    assert guidance.eps_from_tables(sem_ajustado)["eps"] == 1.05


@pytest.mark.parametrize("tabela", [
    "<table><tr><td>Fiscal 2027 Outlook</td></tr><tr><td>Adjusted EPS</td><td>$2.20</td><td>$2.30</td></tr></table>",  # previsão
    "<table><tr><td>Adjusted EPS</td><td>$2.23</td><td>+20% — +25%</td></tr></table>",                                  # intervalo
    "<table><tr><td>Nine Months Ended</td></tr><tr><td>Diluted EPS</td><td>4.15</td></tr></table>",                       # só 9 meses
    "<table><tr><td>Basic earnings per share</td><td>0.40</td></tr></table>",                                             # só básico
    "<table><tr><td>Diluted earnings per share</td><td>7.8532</td></tr></table>",                                         # 4 casas
    "<table><tr><td>Dividends per share</td><td>0.25</td></tr></table>",                                                  # dividendo
])
def test_eps_das_tabelas_ignora_previsoes_e_outros_valores(tabela):
    import guidance
    assert guidance.eps_from_tables(tabela) is None


def test_antecipado_le_o_eps_da_tabela_quando_o_texto_nao_chega(sec_env):
    sec_env["release"].update(texto="Revenue grew 12% to $1.2 billion.", html=TABELA)
    state = {}
    bot.task_check(sec_env["cfg"], state, sec_env["now"], dry=False)
    assert state["sec"]["XYZ|2026-11-10"]["eps"] == 1.32


# ---------------------------------------------------------------- estratégia: comprar no alerta, vender na abertura

def _premarket_resumo(tmp_path, monkeypatch):
    import json as _json
    s = lambda media, liq: {"n": 500, "media": media, "mediana": media, "pct_sobe": 60.0, "liquido": liq}
    resumo = {"custo_ida_volta_pct": 0.3,
              "nomes_saidas": {"abertura": "na abertura", "fecho": "no fecho", "s1030": "às 10:30"},
              "saidas": {
                  "ah17": {"todos": {"saidas": {"abertura": s(1.01, 0.71)}, "melhor": "abertura"},
                           "AMC": {"saidas": {"abertura": s(1.01, 0.71)}, "melhor": "abertura"}},
                  "pm08": {"todos": {"saidas": {"fecho": s(0.52, 0.22)}, "melhor": "fecho"},
                           "AMC": {"saidas": {"abertura": s(0.50, 0.20)}, "melhor": "abertura"},
                           "BMO": {"saidas": {"fecho": s(0.70, 0.40)}, "melhor": "fecho"}},
                  "pm09": {"todos": {"saidas": {"fecho": s(0.37, 0.07)}, "melhor": "fecho"}}}}
    (tmp_path / "premarket_resumo.json").write_text(_json.dumps(resumo), encoding="utf-8")
    monkeypatch.setattr(bot, "DATA", tmp_path)
    bot._premarket_memo.clear()


@pytest.mark.parametrize("kind,momento,early,ny_time,expected", [
    ("after-hours", "AMC", True, (16, 30), ["vender na abertura", "+1.0%", "+0.7% depois"]),    # alerta ⚡
    ("after-hours", "AMC", False, (20, 35), ["pre-market de amanhã", "vender na abertura", "+0.2% depois"]),
    ("pre-market", "BMO", False, (7, 45), ["comprar já", "vender no fecho", "+0.4% depois"]),   # antes da abertura: no fecho
    ("pre-market", "BMO", False, (9, 5), ["vender no fecho", "quase não compensa"]),            # 09:00: sem momento separado
    ("sessão", "BMO", False, (11, 0), None),                                                    # a abertura já passou
])
def test_dica_historica_diz_quando_comprar_e_vender(tmp_path, monkeypatch, kind, momento, early, ny_time, expected):
    _premarket_resumo(tmp_path, monkeypatch)
    now = datetime(2026, 11, 10, *ny_time, tzinfo=bot.NY)
    hint = bot.historical_hint({"movimento_tipo": kind, "momento": momento}, now, early)
    assert (hint is None) if expected is None else all(x in hint for x in expected), hint


def test_sem_resumo_do_pre_market_nao_ha_dica(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DATA", tmp_path)
    bot._premarket_memo.clear()
    assert bot.historical_hint({"movimento_tipo": "pre-market"}, datetime(2026, 11, 10, 8, tzinfo=bot.NY), False) is None


def test_lembrete_antes_da_abertura_diz_quando_vender_cada_alerta(tmp_path, monkeypatch):
    _premarket_resumo(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(bot, "notify", lambda cfg, title, body, **kw: sent.append((title, body)))
    monkeypatch.setattr(bot.yf, "Ticker", lambda s: FakeTicker(info("PRE", ts(2026, 11, 10, 16), pre=3.0)))
    amc = {**event(2026, 11, 10, 16, 5), "simbolo": "XYZ", "nome": "XYZ", "mercado": "NASDAQ", "dia": "2026-11-10",
           "momento": "AMC", "surpresa_pct": 20.0, "movimento_alerta_pct": 1.0, "tipo_alerta": "after-hours",
           "antecipado": True, "alerta_em": datetime(2026, 11, 10, 17, 10, tzinfo=bot.NY).isoformat()}
    bmo = {**event(2026, 11, 11, 7, 0), "simbolo": "ABC", "nome": "ABC", "mercado": "NYSE", "dia": "2026-11-11",
           "momento": "BMO", "surpresa_pct": 15.0, "movimento_alerta_pct": 0.5, "tipo_alerta": "pre-market",
           "antecipado": False, "alerta_em": datetime(2026, 11, 11, 7, 40, tzinfo=bot.NY).isoformat()}
    now = datetime(2026, 11, 11, 9, 0, tzinfo=bot.NY)
    bot.task_premarket(dict(bot.DEFAULTS), {"positivos": {"XYZ|2026-11-10": amc, "ABC|2026-11-11": bmo}}, now, dry=False)
    title, body = sent[0]
    assert title.startswith("🔔 Antes da abertura") and "quando vender" in title
    assert "XYZ: EPS +20.0% · no alerta (22:10) +1.0% · agora +3.0% (pre-market) (+2.0% desde o alerta)" in body
    assert "XYZ" in body.split("→ vender na abertura")[0]
    assert "ABC" in body and "→ vender no fecho (histórico +0.4% depois de custos)" in body


def test_lembrete_sem_resumo_do_pre_market_sugere_a_abertura(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DATA", tmp_path)
    bot._premarket_memo.clear()
    sent = []
    monkeypatch.setattr(bot, "notify", lambda cfg, title, body, **kw: sent.append(body))
    monkeypatch.setattr(bot.yf, "Ticker", lambda s: FakeTicker(info("PRE", ts(2026, 11, 10, 16), pre=3.0)))
    p = {**event(2026, 11, 10, 16, 5), "simbolo": "XYZ", "nome": "XYZ", "mercado": "NASDAQ", "dia": "2026-11-10",
         "momento": "AMC", "surpresa_pct": 20.0}
    bot.task_premarket(dict(bot.DEFAULTS), {"positivos": {"XYZ|2026-11-10": p}}, datetime(2026, 11, 11, 9, 0, tzinfo=bot.NY), dry=False)
    assert "→ vender na abertura" in sent[0]


def test_registo_guarda_a_hora_e_o_preco_do_alerta_antecipado(sec_env, monkeypatch):
    rows = []
    monkeypatch.setattr(bot, "append_log", rows.append)
    state = {}
    bot.task_check(sec_env["cfg"], state, sec_env["now"], dry=False)       # alerta ⚡ com o preço a +6%
    assert state["positivos"]["XYZ|2026-11-10"]["movimento_alerta_pct"] == 6.0
    sec_env["move"]["post"] = 9.0                                            # quando o Yahoo publica, já vai em +9%
    sec_env["yahoo"]["eventos"][0].update(eps_real=1.21, surpresa_pct=21.0)
    later = sec_env["now"] + pd.Timedelta(hours=4)
    bot.task_check(sec_env["cfg"], state, later, dry=False)
    row = rows[-1]
    assert row["alerta_cedo"] == "sim" and row["movimento_alerta_pct"] == 6.0 and row["alerta_em"] == "2026-11-10 21:10"
    assert state["positivos"]["XYZ|2026-11-10"]["movimento_alerta_pct"] == 6.0  # o Yahoo não apaga o preço do alerta


def test_melhor_saida_e_a_mais_cedo_perto_da_melhor():
    import premarket
    rows = []
    for i in range(40):
        rows.append({"ah17": 100.0, "pm08": 100.3, "pm09": 100.4, "abertura": 101.0, "s1030": 101.0, "s1230": 101.1,
                     "s1430": 100.9, "fecho": 102.0 if i % 2 else 100.0})
    r = premarket.exits(pd.DataFrame(rows), "ah17", cost=0.15)
    assert r["saidas"]["abertura"]["liquido"] == pytest.approx(0.7)
    assert r["melhor"] == "abertura"  # 12:30 rende só +0.1 pontos: não compensa esperar
    rows2 = [{**x, "fecho": 102.0} for x in rows]
    assert premarket.exits(pd.DataFrame(rows2), "ah17", cost=0.15)["melhor"] == "fecho"  # +1 ponto: vale a pena


def _positivo_bmo():
    return {**event(2026, 11, 11, 7, 0), "simbolo": "ABC", "nome": "ABC Corp", "mercado": "NYSE", "dia": "2026-11-11",
            "momento": "BMO", "surpresa_pct": 15.0, "movimento_alerta_pct": 0.5, "tipo_alerta": "pre-market",
            "antecipado": False, "alerta_em": datetime(2026, 11, 11, 7, 40, tzinfo=bot.NY).isoformat()}


def test_aviso_de_venda_a_hora_sugerida(tmp_path, monkeypatch):
    _premarket_resumo(tmp_path, monkeypatch)  # antes da abertura, comprado às 08:00: vender no fecho
    sent = []
    monkeypatch.setattr(bot, "notify", lambda cfg, title, body, **kw: sent.append((title, body)))
    monkeypatch.setattr(bot.yf, "Ticker", lambda s: FakeTicker({"regularMarketChangePercent": 2.5}))
    state = {"positivos": {"ABC|2026-11-11": _positivo_bmo()}}
    at = lambda h, m: datetime(2026, 11, 11, h, m, tzinfo=bot.NY)
    bot.task_sell_reminders(dict(bot.DEFAULTS), state, at(15, 40), dry=False)
    assert sent == []                                            # ainda não são 15:45
    bot.task_sell_reminders(dict(bot.DEFAULTS), state, at(15, 46), dry=False)
    bot.task_sell_reminders(dict(bot.DEFAULTS), state, at(15, 48), dry=False)
    assert len(sent) == 1                                        # avisa uma só vez
    title, body = sent[0]
    assert title == "🔔 Hora de vender ABC (20:45)"
    assert "Desde o alerta: +2.0%" in body and "vender no fecho rendeu +0.4% depois de custos" in body


def test_aviso_de_venda_nao_sai_atrasado_nem_para_vendas_na_abertura(tmp_path, monkeypatch):
    _premarket_resumo(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(bot, "notify", lambda cfg, title, body, **kw: sent.append(title))
    monkeypatch.setattr(bot.yf, "Ticker", lambda s: FakeTicker({"regularMarketChangePercent": 1.0}))
    amc = {**event(2026, 11, 10, 16, 5), "simbolo": "XYZ", "nome": "XYZ", "mercado": "NASDAQ", "dia": "2026-11-10",
           "momento": "AMC", "surpresa_pct": 20.0, "movimento_alerta_pct": 1.0, "tipo_alerta": "after-hours",
           "antecipado": True, "alerta_em": datetime(2026, 11, 10, 17, 10, tzinfo=bot.NY).isoformat()}
    state = {"positivos": {"XYZ|2026-11-10": amc, "ABC|2026-11-11": _positivo_bmo()}}
    bot.task_sell_reminders(dict(bot.DEFAULTS), state, datetime(2026, 11, 11, 17, 0, tzinfo=bot.NY), dry=False)
    assert sent == []  # XYZ vende-se na abertura (vai no lembrete das 14:00); ABC: o bot só passou 75 min depois


def test_dia_da_reacao():
    assert bot.reaction_day(event(2026, 11, 10, 7)) == datetime(2026, 11, 10).date()      # antes da abertura: no próprio dia
    assert bot.reaction_day(event(2026, 11, 10, 16, 5)) == datetime(2026, 11, 11).date()  # depois do fecho: no dia seguinte
    assert bot.reaction_day(event(2026, 11, 13, 16, 5)) == datetime(2026, 11, 16).date()  # sexta à noite: segunda


# ---------------------------------------------------------------- excerto do guidance e token do ntfy

@pytest.mark.parametrize("frase,fica", [
    ("For the fourth quarter of 2026, the company expects revenue of $1.30 billion to $1.35 billion.", True),
    ("Raised full year 2026 guidance to adjusted EPS of $4.10 to $4.20.", True),
    ("Full year revenue of $5.23 billion, landing within the guidance range for fiscal 2026.", False),       # resultado conhecido
    ("The fiscal 2025 EPS base of $2.83 represents the midpoint of fiscal 2025 guidance.", False),          # só anos passados
    ("Revenue was $7.53 million, compared with $15.66 million in Q3 2025, below our outlook.", False),      # comparação com o passado
    ("Unrecognized compensation costs of $7 million are expected to be recognized in 2027.", False),        # contabilidade
    ("We expect 2027 revenue of $5 billion, compared with $4.6 billion in 2026.", True),                    # previsão com comparação
])
def test_excerto_do_guidance_deixa_de_fora_o_passado(frase, fica):
    import guidance
    assert bool(guidance.extract(frase, year=2026)) == fica


def test_ntfy_usa_o_token_quando_existe(monkeypatch):
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(bot.requests, "post", lambda url, json, headers, timeout: calls.append(headers) or Resp())
    cfg = {**bot.DEFAULTS, "ntfy_topico": "t"}
    bot.notify(cfg, "a", "b")
    bot.notify({**cfg, "ntfy_token": "tk_123"}, "a", "b")
    assert calls == [{}, {"Authorization": "Bearer tk_123"}]
