"""Gera ESPECIFICACAO.pdf: especificação funcional e técnica do bot de resultados.

Os números vêm dos ficheiros do projeto (config.json, data/registo.csv, data/historico_resumo.json e
data/premarket_resumo.json), por isso basta voltar a correr o script para o documento ficar atualizado.
Paleta da dashboard (docs/index.html): neutros quentes, verde = subida, vermelho = descida, azul = destaque.

Precisa do reportlab (não faz parte do bot):  .venv\\Scripts\\pip install reportlab
Usa as fontes do Windows (Segoe UI e Consolas).

Uso:  .venv\\Scripts\\python.exe scripts\\gerar_especificacao.py [--saida ESPECIFICACAO.pdf]
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import random
import statistics as st
import subprocess
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.colors import HexColor as HC
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FD = r"C:\Windows\Fonts"
for _name, _f in (("S", "segoeui"), ("SB", "segoeuib"), ("SI", "segoeuii"), ("M", "consola")):
    pdfmetrics.registerFont(TTFont(_name, f"{FD}\\{_f}.ttf"))
pdfmetrics.registerFontFamily("S", normal="S", bold="SB", italic="SI", boldItalic="SB")
pdfmetrics.registerFont(TTFont("SY", f"{FD}\\seguisym.ttf"))  # símbolos que a Segoe UI não tem (⚡ 🔔 ⚠ ↩ ✅ 💡)
SYMBOLS = set("⚡🔔⚠↩✅💡")


def sym(text: str) -> str:
    """Põe os símbolos na fonte Segoe UI Symbol (para texto de Paragraph)."""
    text = text.replace("️", "")
    return "".join(f'<font name="SY">{ch}</font>' if ch in SYMBOLS else ch for ch in text)


def draw_text(c, x, y, text, font, size, right=False):
    """drawString que troca para a Segoe UI Symbol nos símbolos."""
    text = text.replace("️", "")
    parts, cur = [], ""
    for ch in text:
        if ch in SYMBOLS:
            if cur:
                parts.append((cur, font))
            parts.append((ch, "SY"))
            cur = ""
        else:
            cur += ch
    if cur:
        parts.append((cur, font))
    width = sum(pdfmetrics.stringWidth(t, f, size) for t, f in parts)
    x = x - width if right else x
    for t, f in parts:
        c.setFont(f, size)
        c.drawString(x, y, t)
        x += pdfmetrics.stringWidth(t, f, size)

# ---------------------------------------------------------------- paleta (dashboard)
PAGE, SURF, INK, INK2, MUTED = HC("#f9f9f7"), HC("#fcfcfb"), HC("#0b0b0b"), HC("#52514e"), HC("#898781")
LINE, CHIP, AXIS = HC("#e1e0d9"), HC("#f0efec"), HC("#c3c2b7")
UP, UPB, DOWN, DOWNB, ACC = HC("#006300"), HC("#0ca30c"), HC("#d03b3b"), HC("#e66767"), HC("#2a78d6")
DK, DK2, DKLINE, AMBER = HC("#0d0d0d"), HC("#1a1a19"), HC("#2c2c2a"), HC("#b7791f")
W = 174 * mm

base = ParagraphStyle("b", fontName="S", fontSize=9.5, leading=13.8, textColor=HC("#2b2a28"), spaceAfter=5)
cell = ParagraphStyle("c", parent=base, fontSize=8.4, leading=11.2, spaceAfter=0)
cellh = ParagraphStyle("ch", parent=cell, fontName="SB", textColor=colors.white, fontSize=8.2)
cellr = ParagraphStyle("cr", parent=cell, alignment=TA_RIGHT)
cellhr = ParagraphStyle("chr", parent=cellh, alignment=TA_RIGHT)
small = ParagraphStyle("s", parent=base, fontSize=8.2, leading=11, textColor=INK2, spaceAfter=0)
h2 = ParagraphStyle("h2", parent=base, fontName="SB", fontSize=10.8, leading=14, textColor=INK, spaceBefore=9, spaceAfter=4, keepWithNext=1)
bullet = ParagraphStyle("bl", parent=base, leftIndent=13, bulletIndent=2, spaceAfter=3)

story: list = []
P = lambda t, s=base: story.append(Paragraph(sym(t), s))
H2 = lambda t: story.append(Paragraph(sym(t), h2))


def pt(v, nd=2, sign=False, suf=""):
    """Número à portuguesa: vírgula decimal, menos tipográfico, espaço nas milhares."""
    if v is None:
        return "–"
    s = f"{v:+,.{nd}f}" if sign else f"{v:,.{nd}f}"
    s = s.replace(",", "\u00a0").replace(".", ",").replace("-", "−")
    if sign and float(f"{v:.{nd}f}") == 0:
        s = s.replace("+", "").replace("−", "")
    return s + suf


def tone(v, nd=2, suf="%"):
    if v is None:
        return "–"
    c = "#2b2a28" if round(v, nd) == 0 else ("#006300" if v > 0 else "#d03b3b")
    return f'<font color="{c}"><b>{pt(v, nd, True, suf)}</b></font>'


def code(t):
    return f'<font name="M" size="8" color="#0b0b0b" backColor="#ece9e1">&nbsp;{t}&nbsp;</font>'


def B(items):
    for t in items:
        story.append(Paragraph(sym(t), bullet, bulletText="•"))
    story.append(Spacer(1, 3))


def T(rows, widths, head=True, aligns=None):
    """Tabela com cabeçalho escuro, linhas alternadas e cantos arredondados."""
    aligns = aligns or "L" * len(widths)
    data = []
    for i, r in enumerate(rows):
        line = []
        for j, c in enumerate(r):
            hd = head and i == 0
            line.append(Paragraph(sym(str(c)), (cellhr if aligns[j] == "R" else cellh) if hd else (cellr if aligns[j] == "R" else cell)))
        data.append(line)
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1 if head else 0, cornerRadii=[4, 4, 4, 4])
    st_ = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
           ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
           ("ROWBACKGROUNDS", (0, 1 if head else 0), (-1, -1), [SURF, CHIP]), ("BOX", (0, 0), (-1, -1), 0.6, LINE)]
    if head:
        st_ += [("BACKGROUND", (0, 0), (-1, 0), DK2)]
    t.setStyle(TableStyle(st_))
    story.append(KeepTogether([t]) if len(rows) <= 6 else t)
    story.append(Spacer(1, 9))


def callout(text, color=ACC, label=None):
    body = (f'<font name="SB" color="{color.hexval().replace("0x", "#")}" size="8">{label.upper()}</font><br/>' if label else "") + text
    t = Table([[Paragraph(sym(body), ParagraphStyle("co", parent=base, fontSize=9.2, leading=13, spaceAfter=0))]], colWidths=[W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SURF), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                           ("LINEBEFORE", (0, 0), (0, -1), 3.2, color), ("LEFTPADDING", (0, 0), (-1, -1), 11),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 7),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story.append(t)
    story.append(Spacer(1, 9))


def kpis(items):
    n, gap = len(items), 4
    cw = (174 - gap * (n - 1)) / n
    row, widths = [], []
    for i, (v, lab, col) in enumerate(items):
        card = Table([[Paragraph(f'<font name="SB" size="19" color="{col}">{v}</font>', ParagraphStyle("kv", parent=base, leading=23, spaceAfter=0))],
                      [Paragraph(sym(lab), ParagraphStyle("kl", parent=small, fontSize=7.8, leading=10))]], colWidths=[cw * mm])
        card.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SURF), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                                  ("LINEABOVE", (0, 0), (-1, 0), 2.4, HC(col)), ("TOPPADDING", (0, 0), (-1, -1), 5),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
        row.append(card)
        widths.append(cw * mm)
        if i < n - 1:
            row.append("")
            widths.append(gap * mm)
    t = Table([row], colWidths=widths)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")] + [(k, (0, 0), (-1, -1), 0) for k in
                                                                     ("LEFTPADDING", "RIGHTPADDING", "TOPPADDING", "BOTTOMPADDING")]))
    story.append(t)
    story.append(Spacer(1, 10))


# ---------------------------------------------------------------- elementos gráficos
PAGES: dict = {}


class Section(Flowable):
    def __init__(self, num, text):
        super().__init__()
        self.num, self.text, self.keepWithNext = num, text, 1

    def wrap(self, aw, ah):
        self.aw = aw
        return aw, 16 * mm

    def draw(self):
        c = self.canv
        PAGES[self.num] = c.getPageNumber()
        c.setFillColor(UP)
        c.roundRect(0, 3.2 * mm, 9 * mm, 9 * mm, 2.2 * mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("SB", 11)
        c.drawCentredString(4.5 * mm, 5.9 * mm, str(self.num))
        c.setFillColor(INK)
        c.setFont("SB", 15.5)
        c.drawString(13 * mm, 5.6 * mm, self.text)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.7)
        c.line(0, 1 * mm, self.aw, 1 * mm)


class FlowDiagram(Flowable):
    """Fontes → bot → saídas."""
    H = 64 * mm

    def wrap(self, aw, ah):
        return aw, self.H

    def box(self, x, y, w, h, title, subtitle, dark=False):
        c = self.canv
        c.setFillColor(DK2 if dark else SURF)
        c.setStrokeColor(DK2 if dark else LINE)
        c.roundRect(x, y, w, h, 2.5 * mm, fill=1, stroke=1)
        c.setFillColor(colors.white if dark else INK)
        draw_text(c, x + 3.5 * mm, y + h - 6 * mm, title, "SB", 8.6)
        c.setFillColor(AXIS if dark else MUTED)
        for k, line in enumerate(subtitle.split("\n")):
            draw_text(c, x + 3.5 * mm, y + h - 10.2 * mm - k * 3.4 * mm, line, "S", 6.9)

    def arrow(self, x0, y0, x1, y1):
        c = self.canv
        c.setStrokeColor(AXIS)
        c.setFillColor(AXIS)
        c.setLineWidth(1)
        c.line(x0, y0, x1 - 2 * mm, y1)
        p = c.beginPath()
        p.moveTo(x1, y1)
        p.lineTo(x1 - 2.4 * mm, y1 + 1.2 * mm)
        p.lineTo(x1 - 2.4 * mm, y1 - 1.2 * mm)
        p.close()
        c.drawPath(p, fill=1, stroke=0)

    def draw(self):
        c = self.canv
        bh, gap = 18 * mm, 5 * mm
        lw, mw, rw = 48 * mm, 56 * mm, 48 * mm
        xl, xm, xr = 0, 59 * mm, 126 * mm
        ys = [self.H - bh - i * (bh + gap) for i in range(3)]
        src = [("SEC EDGAR (⚡)", "Feed de entregas recentes:\ncomunicado minutos depois"),
               ("Yahoo Finance", "Calendário, EPS (horas depois),\npreços, estimativas"),
               ("Nasdaq", "Lista de cotadas e\ncalendário alternativo")]
        out_ = [("ntfy → telemóvel", "Alertas, quando vender,\navisos de falhas"),
                ("Repositório (data/)", "Estado, registo.csv,\ntestes históricos"),
                ("Dashboard", "GitHub Pages, lê os\nficheiros do repositório")]
        for (t, s), y in zip(src, ys):
            self.box(xl, y, lw, bh, t, s)
        for (t, s), y in zip(out_, ys):
            self.box(xr, y, rw, bh, t, s)
        self.box(xm, 0, mw, self.H, "bot.py auto  ·  GitHub Actions",
                 "manha · calendário às 08:30\nverificar · SEC ⚡ e Yahoo\nantes da abertura · quando vender\n"
                 "venda · aviso à hora de vender\nrever · reação do preço (à noite)\n\npassagem de 2 em 2 minutos\n"
                 "vigia.py · avisa se o bot parar", dark=True)
        c.setFillColor(UPB)
        c.circle(xm + mw - 5 * mm, self.H - 5.4 * mm, 1.3 * mm, fill=1, stroke=0)
        for y in ys:
            self.arrow(lw, y + bh / 2, xm, y + bh / 2)
            self.arrow(xm + mw, y + bh / 2, xr, y + bh / 2)


class RuleChips(Flowable):
    """Os três requisitos de um alerta positivo."""
    H = 19 * mm

    def __init__(self, items):
        super().__init__()
        self.items = items

    def wrap(self, aw, ah):
        return aw, self.H

    def draw(self):
        c = self.canv
        bw, h = 41 * mm, self.H
        x = 0
        for i, (a, b) in enumerate(self.items):
            c.setFillColor(SURF)
            c.setStrokeColor(LINE)
            c.roundRect(x, 0, bw, h, 2.5 * mm, fill=1, stroke=1)
            c.setFillColor(ACC)
            c.setFont("SB", 7)
            c.drawString(x + 3.5 * mm, h - 6 * mm, a)
            c.setFillColor(INK)
            c.setFont("SB", 8.6)
            c.drawString(x + 3.5 * mm, h - 11.5 * mm, b)
            x += bw
            if i < len(self.items) - 1:
                c.setFillColor(MUTED)
                c.setFont("SB", 12)
                c.drawCentredString(x + 3 * mm, h / 2 - 1.5 * mm, "+")
                x += 6 * mm
        c.setFillColor(MUTED)
        c.setFont("SB", 13)
        c.drawCentredString(x + 4 * mm, h / 2 - 2 * mm, "→")
        x += 9 * mm
        c.setFillColor(UP)
        c.roundRect(x, 0, W - x, h, 2.5 * mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("SB", 11)
        c.drawString(x + 4 * mm, h / 2 + 0.6 * mm, "ALERTA")
        c.setFont("S", 6.8)
        c.drawString(x + 4 * mm, h / 2 - 4 * mm, "comprar · vender à hora")


class HBars(Flowable):
    """Barras horizontais divergentes: (rótulo, valor)."""

    def __init__(self, items, label_w=66 * mm, row=6.6 * mm):
        super().__init__()
        self.items, self.lw, self.row = items, label_w, row

    def wrap(self, aw, ah):
        self.aw = aw
        return aw, len(self.items) * self.row + 4 * mm

    def draw(self):
        c, vals = self.canv, [v for _, v in self.items]
        lo, hi = min(0, min(vals)), max(0, max(vals))
        x0p, x1p = self.lw + 13 * mm, self.aw - 14 * mm
        k = (x1p - x0p) / ((hi - lo) or 1)
        zx = x0p + (-lo) * k
        top = len(self.items) * self.row + 2 * mm
        for i, (lab, v) in enumerate(self.items):
            y = top - (i + 1) * self.row
            c.setFillColor(INK2)
            c.setFont("S", 7.8)
            c.drawString(0, y + 2 * mm, lab)
            w = abs(v) * k
            c.setFillColor(UPB if v > 0 else DOWNB if v < 0 else AXIS)
            c.roundRect(zx if v >= 0 else zx - w, y + 1.2 * mm, max(w, 0.6), self.row - 2.6 * mm, 0.8 * mm, fill=1, stroke=0)
            c.setFillColor(INK)
            c.setFont("SB", 7.4)
            s = pt(v, 2, True, "%")
            if v >= 0:
                c.drawString(zx + w + 1.6 * mm, y + 2.1 * mm, s)
            else:
                c.drawRightString(zx - w - 1.6 * mm, y + 2.1 * mm, s)
        c.setStrokeColor(AXIS)
        c.setLineWidth(0.8)
        c.line(zx, 0, zx, top)


class VBars(Flowable):
    """Uma barra por alerta real."""
    H = 62 * mm

    def __init__(self, items):
        super().__init__()
        self.items = items

    def wrap(self, aw, ah):
        self.aw = aw
        return aw, self.H

    def draw(self):
        c = self.canv
        lab_h, left = 13 * mm, 9 * mm
        ph = self.H - lab_h - 2 * mm
        vals = [v for _, v in self.items]
        lim = 5 * (int(max(abs(min(vals)), abs(max(vals))) / 5) + 1)
        k = (ph / 2) / lim
        zy = lab_h + ph / 2
        for g in range(-lim, lim + 1, 5):
            y = zy + g * k
            c.setStrokeColor(AXIS if g == 0 else LINE)
            c.setLineWidth(0.8 if g == 0 else 0.4)
            c.line(left, y, self.aw, y)
            c.setFillColor(MUTED)
            c.setFont("S", 6.4)
            c.drawRightString(left - 1.5 * mm, y - 0.8 * mm, f"{g:+d}%" if g else "0")
        slot = (self.aw - left) / len(self.items)
        for i, (lab, v) in enumerate(self.items):
            x = left + i * slot + slot * 0.16
            h = v * k
            c.setFillColor(UPB if v >= 0 else DOWNB)
            c.roundRect(x, zy if h >= 0 else zy + h, slot * 0.68, max(abs(h), 0.5), 0.6 * mm, fill=1, stroke=0)
            c.saveState()
            c.translate(x + slot * 0.34 + 1.1, lab_h - 1.6 * mm)
            c.rotate(-90)
            c.setFillColor(INK2)
            c.setFont("S", 6.5)
            c.drawRightString(0, 0, lab)
            c.restoreState()


class Index(Flowable):
    ROW = 13.2 * mm

    def __init__(self, items, desc):
        super().__init__()
        self.items, self.desc = items, desc

    def wrap(self, aw, ah):
        self.aw, self.H = aw, 36 * mm + len(self.items) * self.ROW
        return aw, self.H

    def draw(self):
        c, H = self.canv, self.H
        c.setFillColor(UPB)
        c.setFont("SB", 8.5)
        c.drawString(0, H - 4 * mm, "CONTEÚDO")
        c.setFillColor(INK)
        c.setFont("SB", 28)
        c.drawString(0, H - 17 * mm, "Índice")
        c.setStrokeColor(LINE)
        c.setLineWidth(0.7)
        c.line(0, H - 22 * mm, self.aw, H - 22 * mm)
        y = H - 34 * mm
        for num, text in self.items:
            c.setFillColor(UP)
            c.roundRect(0, y - 1.2 * mm, 8.5 * mm, 8.5 * mm, 2.2 * mm, fill=1, stroke=0)
            c.setFillColor(colors.white)
            c.setFont("SB", 10)
            c.drawCentredString(4.25 * mm, y + 1.4 * mm, str(num))
            c.setFillColor(INK)
            c.setFont("SB", 11)
            c.drawString(13 * mm, y + 3.3 * mm, text)
            c.setFillColor(MUTED)
            c.setFont("S", 7.8)
            c.drawString(13 * mm, y - 0.6 * mm, self.desc.get(num, ""))
            c.setFillColor(INK)
            c.setFont("SB", 11)
            c.drawRightString(self.aw, y + 3.3 * mm, str(PAGES.get(num, "–")))
            x0 = 13 * mm + pdfmetrics.stringWidth(text, "SB", 11) + 3 * mm
            c.setStrokeColor(AXIS)
            c.setLineWidth(0.8)
            c.setDash(0.6, 2.6)
            c.line(x0, y + 3.7 * mm, self.aw - 9 * mm, y + 3.7 * mm)
            c.setDash()
            y -= self.ROW


# ---------------------------------------------------------------- dados do projeto
def load():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    reg = list(csv.DictReader((DATA / "registo.csv").open(encoding="utf-8-sig"), delimiter=";"))
    hist = json.loads((DATA / "historico_resumo.json").read_text(encoding="utf-8"))
    pre = json.loads((DATA / "premarket_resumo.json").read_text(encoding="utf-8"))
    try:
        # último commit que mexeu no código (os do bot só mudam data/)
        commit = subprocess.check_output(["git", "-C", str(ROOT), "log", "-1", "--format=%h (%ad)", "--date=format:%d/%m/%Y",
                                          "--", ".", ":!data", ":!ESPECIFICACAO.pdf"], text=True).strip()
    except Exception:
        commit = "?"
    return cfg, reg, hist, pre, commit


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def from_alert(r):
    """Ganho de comprar no alerta e vender na abertura (como na dashboard)."""
    if r["movimento_tipo"] not in ("pre-market", "after-hours") or not r.get("fecho"):
        return None
    a = num(r.get("movimento_alerta_pct")) if r.get("movimento_alerta_pct") else num(r["movimento_pct"])
    g = num(r["abertura_vs_fecho_ant_pct"])
    if a is None or g is None:
        return None
    return ((1 + g / 100) / (1 + a / 100) - 1) * 100


def build(out: Path):
    cfg, reg, hist, pre, commit = load()
    today = date.today().strftime("%d/%m/%Y")
    fd = lambda s: f"{s[8:10]}/{s[5:7]}/{s[:4]}"
    pos = [r for r in reg if r["positivo"] == "sim"]
    early = [r for r in pos if r.get("alerta_cedo") == "sim"]
    fa = [(r["simbolo"], v) for r in pos if (v := from_alert(r)) is not None]
    delays = [num(r["atraso_min"]) for r in reg if r["atraso_min"] and r["movimento_tipo"] != "tardio"]
    sec_delays = [num(r["atraso_sec_min"]) for r in reg if r.get("atraso_sec_min")]
    d0, d1 = min(r["data_resultados"] for r in reg), max(r["data_resultados"] for r in reg)
    smin, mlo, mhi = cfg["surpresa_eps_minima_pct"], cfg["movimento_preco_minimo_pct"], cfg.get("movimento_preco_maximo_pct")
    capmin = cfg.get("capitalizacao_minima_alerta_usd")
    crit = (f"surpresa do EPS ≥ {smin:g}% e movimento do preço " +
            (f"entre {pt(mlo, 0, True)}% e {pt(mhi, 0, True)}%" if mhi is not None else f"≥ {pt(mlo, 0, True)}%") +
            (f" e capitalização ≥ {capmin / 1e9:g} mil M$" if capmin else ""))
    ah = pre["saidas"].get("ah17", {}).get("AMC", {})
    ah_best = ah.get("saidas", {}).get(ah.get("melhor"), {}) if ah else {}
    names = pre.get("nomes_saidas", {})

    # ------------------------------------------------------------ capa
    def cover(c, d):
        w, h = A4
        c.setFillColor(DK)
        c.rect(0, 0, w, h, fill=1, stroke=0)
        c.setFillColor(UPB)
        c.rect(0, h - 5, 90, 5, fill=1, stroke=0)
        c.circle(42, 786, 4, fill=1, stroke=0)
        c.setFillColor(AXIS)
        c.setFont("SB", 8.2)
        c.drawString(52, 783, "BOT DE RESULTADOS")
        c.setFillColor(MUTED)
        c.setFont("S", 8.2)
        c.drawRightString(w - 40, 783, f"v2.0 · {today}")
        c.setFillColor(UPB)
        c.setFont("SB", 9.5)
        c.drawString(42, 690, "ESPECIFICAÇÃO FUNCIONAL E TÉCNICA")
        c.setFillColor(colors.white)
        c.setFont("SB", 44)
        c.drawString(40, 636, "Bot de Resultados")
        c.drawString(40, 586, "Trimestrais")
        sub = Paragraph("Deteta os resultados das empresas cotadas nos EUA minutos depois de saírem, avisa no telemóvel "
                        "quando bateram as estimativas e o mercado ainda não reagiu, e diz quando comprar e quando vender.",
                        ParagraphStyle("cs", fontName="S", fontSize=12.5, leading=18, textColor=AXIS))
        _, sh = sub.wrapOn(c, 410, 90)
        sub.drawOn(c, 42, 560 - sh)
        rng = random.Random(11)
        n, x0, x1, y0, y1 = 34, 42, w - 42, 230, 430
        series, p = [], 100.0
        for _ in range(n):
            o = p
            cl = o + rng.gauss(0.9, 3.4)
            series.append((o, cl, max(o, cl) + abs(rng.gauss(0, 2.2)), min(o, cl) - abs(rng.gauss(0, 2.2))))
            p = cl
        lo, hi = min(s[3] for s in series), max(s[2] for s in series)
        Y = lambda v: y0 + (v - lo) / (hi - lo) * (y1 - y0)
        c.setStrokeColor(DKLINE)
        c.setLineWidth(0.5)
        for i in range(5):
            c.line(x0, y0 + i * (y1 - y0) / 4, x1, y0 + i * (y1 - y0) / 4)
        step = (x1 - x0) / n
        for i, (o, cl, hh, ll) in enumerate(series):
            col = UPB if cl >= o else DOWNB
            x = x0 + i * step + step / 2
            c.setStrokeColor(col)
            c.setFillColor(col)
            c.setLineWidth(1)
            c.line(x, Y(ll), x, Y(hh))
            c.rect(x - 3.2, min(Y(o), Y(cl)), 6.4, max(abs(Y(cl) - Y(o)), 1), fill=1, stroke=0)
        lx, ly = x0 + (n - 1) * step + step / 2, Y(series[-1][2])
        c.setStrokeColor(UPB)
        c.setLineWidth(0.8)
        c.line(lx, ly + 2, lx, y1 + 22)
        c.setFillColor(DK2)
        c.roundRect(x1 - 290, y1 + 22, 290, 26, 6, fill=1, stroke=1)
        c.setFillColor(UPB)
        c.circle(x1 - 277, y1 + 35, 3.4, fill=1, stroke=0)
        c.setFillColor(colors.white)
        draw_text(c, x1 - 268, y1 + 32, "⚡ XYZ bateu +18,2%  ·  after-hours +0,8%", "SB", 8.8)
        c.setFillColor(AXIS)
        c.setFont("S", 8.8)
        c.drawRightString(x1 - 12, y1 + 32, "vender na abertura")
        cards = [(str(len(reg)), f"resultados avaliados\n({fd(d0)[:5]} a {fd(d1)})", colors.white),
                 (str(len(pos)), "alertas positivos\nenviados", UPB),
                 (pt(pre["relatorios"], 0), "relatórios no teste\ndo pre-market", colors.white),
                 (pt(ah_best.get("liquido"), 2, True, "%"), "⚡ comprar no after-hours,\nvender na abertura (líquido)", UPB)]
        cw, gap = (w - 84 - 3 * 10) / 4, 10
        for i, (v, lab, col) in enumerate(cards):
            x = 42 + i * (cw + gap)
            c.setFillColor(DK2)
            c.setStrokeColor(DKLINE)
            c.roundRect(x, 108, cw, 76, 6, fill=1, stroke=1)
            c.setFillColor(col)
            c.setFont("SB", 24)
            c.drawString(x + 12, 148, v)
            c.setFillColor(MUTED)
            for k, line in enumerate(lab.split("\n")):
                draw_text(c, x + 12, 131 - k * 10.5, line, "S", 7.8)
        c.setStrokeColor(DKLINE)
        c.setLineWidth(0.6)
        c.line(42, 76, w - 42, 76)
        c.setFillColor(MUTED)
        c.setFont("S", 8)
        c.drawString(42, 58, "Python 3.12  ·  GitHub Actions  ·  ntfy  ·  yfinance  ·  SEC EDGAR")
        c.drawRightString(w - 42, 58, "luispereira1999.github.io/bot-resultados")
        c.drawString(42, 44, f"Estado do código: commit {commit}")

    def inner(c, d):
        w, h = A4
        c.saveState()
        c.setFillColor(PAGE)
        c.rect(0, 0, w, h, fill=1, stroke=0)
        c.setFillColor(DK)
        c.rect(0, h - 9 * mm, w, 9 * mm, fill=1, stroke=0)
        c.setFillColor(UPB)
        c.rect(0, h - 9 * mm, 12 * mm, 9 * mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("SB", 7.6)
        c.drawString(18 * mm, h - 5.6 * mm, "BOT DE RESULTADOS")
        c.setFillColor(AXIS)
        c.setFont("S", 7.6)
        c.drawRightString(w - 18 * mm, h - 5.6 * mm, "Especificação funcional e técnica")
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(18 * mm, 14 * mm, w - 18 * mm, 14 * mm)
        c.setFillColor(MUTED)
        c.setFont("S", 7.6)
        c.drawString(18 * mm, 9.5 * mm, f"v2.0 · {today}")
        c.setFillColor(DK)
        c.roundRect(w - 26 * mm, 7.6 * mm, 8 * mm, 5 * mm, 1.6 * mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("SB", 7.6)
        c.drawCentredString(w - 22 * mm, 9.2 * mm, str(d.page))
        c.restoreState()

    # ============================================================ conteúdo
    story.append(PageBreak())

    story.append(Section(1, "Resumo"))
    P("O projeto é um bot pessoal que acompanha a época de resultados das empresas cotadas na NASDAQ e na NYSE "
      "(e numa lista de ações da Euronext). Corre sozinho no GitHub Actions, de 2 em 2 minutos nos dias úteis, e avisa "
      "no telemóvel (app ntfy).")
    P(f"Avisa quando um resultado é <b>positivo</b>: {crit}. Ou seja, a empresa bateu bem as estimativas e o mercado "
      "ainda não reagiu. Para chegar antes do mercado, lê o comunicado na SEC minutos depois de ser entregue (alerta ⚡), "
      "em vez de esperar horas pelo Yahoo. Cada alerta diz quando comprar e quando vender, com o ganho que essa "
      "estratégia teve no teste histórico; antes da abertura e à hora de vender chegam lembretes.")
    P("Todos os resultados ficam num registo, com o que cada ação fez a seguir. Dois testes históricos, recalculados "
      "todos os meses, medem a estratégia: um com preços diários (comprar na abertura) e outro com barras de 1 hora com "
      "after-hours e pre-market (comprar antes da abertura). Uma dashboard pública mostra tudo.")
    callout(f"No teste com barras de 1 hora, comprar no after-hours 1 hora depois do comunicado e vender na abertura "
            f"deu {tone(ah_best.get('media'))} em média ({tone(ah_best.get('liquido'))} depois de "
            f"{pt(pre.get('custo_ida_volta_pct'), 2)}% de custos), {pt(ah_best.get('pct_sobe'), 0)}% das vezes a subir. "
            "O bot não negoceia: só avisa e mede.", UPB, "A estratégia")

    story.append(Section(2, "Arquitetura"))
    story.append(FlowDiagram())
    story.append(Spacer(1, 10))
    T([["Componente", "Ficheiro", "Função"],
       ["Bot principal", code("bot.py"), "Calendário, deteção pela SEC e pelo Yahoo, regras de alerta, avisos ntfy, lembretes de venda, registo e revisão."],
       ["SEC", code("guidance.py"), "Feed de entregas recentes, leitura do EPS do comunicado (texto e tabelas) e excerto do guidance."],
       ["Teste histórico", code("backtest.py"), "Preços diários, 2 anos: compra na abertura; validação fora da amostra e critérios candidatos."],
       ["Teste do pre-market", code("premarket.py"), "Barras de 1 hora com after-hours e pre-market: quando comprar e quando vender, com custos."],
       ["Vigia", code("vigia.py"), "Workflow à parte que avisa se o bot deixar de correr."],
       ["Dashboard", code("docs/index.html"), "Página estática (GitHub Pages) que lê os ficheiros de data/ do repositório."],
       ["Configuração", code("config.json") + " " + code("euronext.txt"), "Critério do alerta, limites e horários; ações europeias a seguir."],
       ["Especificação", code("scripts/gerar_especificacao.py"), "Gera este documento a partir dos dados do projeto."],
       ["Testes", code("tests/test_bot.py"), "Testes pytest, sem internet; correm no GitHub a cada mudança do código."]],
      [30, 50, 94])

    story.append(Section(3, "Funcionalidades"))
    H2("Avisos no telemóvel")
    T([["Quando", "Aviso"],
       [f"08:30 (seg–sex)", f"Calendário dos próximos {cfg['dias_calendario']} dias: empresa, hora, EPS estimado e capitalização."],
       ["Minutos depois do comunicado", "⚡ Alerta antecipado: EPS lido do comunicado na SEC; diz quando comprar e vender."],
       ["Quando o Yahoo publica o EPS", "Alerta normal (se não houve ⚡) ou ↩️ “não se confirma” se o ⚡ leu mal o EPS."],
       [f"{cfg['minutos_antes_abertura_resumo']} min antes da abertura de Nova Iorque", "🔔 Para cada alerta: movimento no alerta e agora, ganho desde o alerta e quando vender."],
       ["À hora de vender (se não for a abertura)", "🔔 “Hora de vender”: 10:30, 12:30, 14:30 ou 15 min antes do fecho de Nova Iorque."],
       ["5 passagens seguidas com erros", "⚠️ O bot (ou a deteção pela SEC) está com erros; ✅ quando volta a funcionar."],
       ["90 min sem corridas (dias úteis)", "⚠️ O bot parou (Vigia), com lembretes às 07:00 e 13:00 UTC."]],
      [56, 118])
    H2("Comandos")
    P("O ponto de entrada é " + code("bot.py &lt;comando&gt; [--seco]") + "; com " + code("--seco") +
      " mostra no ecrã e não envia nada nem grava o estado. O agendador corre " + code("auto") +
      ", que decide o que fazer consoante a hora: " + code("manha") + ", " + code("verificar") + ", " + code("premarket") +
      ", lembretes de venda e " + code("rever") + " (reação do preço, à noite e ao sábado).")

    story.append(Section(4, "Regras de decisão"))
    H2("4.1 Critério do alerta")
    mv = f"entre {pt(mlo, 0, True)}% e {pt(mhi, 0, True)}%" if mhi is not None else f"≥ {pt(mlo, 0, True)}%"
    story.append(RuleChips([("EPS", f"surpresa ≥ {smin:g}%"), ("PREÇO", f"{mv}".replace("entre ", "")), ("TEMPO", "antes da sessão de reação")]))
    story.append(Spacer(1, 8))
    P("A surpresa vem do Yahoo (ou do comunicado, no alerta ⚡); se faltar, é (EPS real − estimado) / |estimado|. O "
      "movimento é o do pre-market ou after-hours face ao fecho anterior. Resultados vistos só depois da sessão de reação "
      "ficam como <b>tardios</b>: registados, nunca alertados. Cada relatório é avaliado uma só vez.")
    H2("4.2 Alerta antecipado (⚡)")
    P("A cada passagem o bot lê o feed de entregas recentes da SEC. Quando uma empresa do calendário entrega o comunicado "
      "de resultados (8-K com o ponto 2.02, ou 6-K), lê o EPS do trimestre no texto (o ajustado, se houver) e, se não "
      "chegar, nas tabelas. Ignora dividendos, preços, previsões, valores do ano anterior ou de períodos longos, e "
      "abstém-se quando não há um valor claro. Se o resultado for positivo, avisa logo; quando o Yahoo publica, confirma "
      "em silêncio ou manda ↩️ e tira a ação do lembrete.")
    callout("Em 126 comunicados reais: EPS lido em 65 (52%); nesses, a decisão “bateu” coincidiu com a do Yahoo em 88%; "
            "2 em 35 alertas teriam sido falsos. Nos outros comunicados o bot espera pelo Yahoo.", ACC, "Precisão")
    H2("4.3 Conteúdo do alerta")
    B(["Título: símbolo, surpresa e movimento (⚡ se antecipado); nome, mercado, EPS real vs estimado.",
       "Próximo trimestre segundo os analistas (EPS e receitas) e revisões das estimativas nos últimos 7 dias.",
       "Guidance: até 3 frases do comunicado (em inglês).",
       "💡 Estratégia: quando comprar e quando vender, com o ganho bruto e líquido no teste do pre-market; "
       "aviso se, depois de custos, quase não compensa.",
       "Botões: comunicado na SEC e página do Yahoo Finance."])

    story.append(Section(5, "Quando comprar e vender"))
    P("O teste do pre-market mede, para o critério do bot, cada momento de compra e de venda, descontando "
      f"{pt(pre.get('custo_ida_volta_pct'), 2)}% de custos ida e volta. A “melhor” venda é a mais cedo que fica a menos de "
      "0,15 pontos da que rende mais; é a que o bot sugere.")
    buy = {"ah17": "⚡ after-hours 17:00", "pm08": "pre-market 08:00", "pm09": "pre-market 09:00"}
    who = {"AMC": "depois do fecho", "BMO": "antes da abertura"}
    rows = [["Compra", "Relatórios", "Melhor venda", "Casos", "Bruto", "Líquido", "Sobem"]]
    for entry, by_m in pre["saidas"].items():
        for m in ("AMC", "BMO"):
            e = by_m.get(m)
            if not e or not e.get("melhor"):
                continue
            s = e["saidas"][e["melhor"]]
            rows.append([buy.get(entry, entry), who[m], names.get(e["melhor"], e["melhor"]), pt(s["n"], 0),
                         tone(s["media"]), tone(s["liquido"]), pt(s["pct_sobe"], 0, False, "%")])
    T(rows, [34, 30, 34, 16, 20, 20, 20], aligns="LLLRRRR")
    callout("O ganho está antes da abertura: comprar com o pre-market já a subir 1–5% não dá vantagem, e comprar perto "
            "da abertura (09:00) não compensa os custos. O alerta ⚡ no after-hours é o que faz a estratégia valer a pena.",
            AMBER, "Leitura")

    story.append(Section(6, "Dados e persistência"))
    T([["Ficheiro", "Conteúdo", "No git"],
       [code("data/estado.json"), "Relatórios vistos, alertas positivos, comunicados da SEC detetados, contadores de falhas.", "Sim"],
       [code("data/registo.csv"), "Um resultado por linha: EPS, surpresa, movimento, alerta (hora e preço), EPS do comunicado, atraso do Yahoo e da SEC, reação do preço.", "Sim"],
       [code("data/calendario.json"), "Próximos relatórios, para a dashboard.", "Sim"],
       [code("data/historico*.{csv,json}"), "Teste histórico diário (todos os casos e resumo).", "Sim"],
       [code("data/premarket*.{csv,json}"), "Teste do pre-market (todos os casos e resumo, incluindo quando vender).", "Sim"],
       [code("cotadas.json") + " " + code("sec_tickers.json") + " " + code("sec_ultimo.txt"), "Caches e marca da última leitura da SEC (mudam a cada passagem).", "Não"]],
      [52, 100, 22])
    P("O estado só muda quando há novidades, para não gerar um commit a cada passagem. A escrita é atómica e o registo "
      "ganha colunas novas sem perder as antigas.")

    story.append(Section(7, "Configuração e segredos"))
    meaning = {"capitalizacao_minima_usd": "Capitalização mínima no calendário (EUA)",
               "surpresa_eps_minima_pct": "Surpresa mínima do EPS para alertar (%)",
               "movimento_preco_minimo_pct": "Movimento mínimo do preço (%)",
               "movimento_preco_maximo_pct": "Movimento máximo do preço (%, vazio = sem máximo)",
               "capitalizacao_minima_alerta_usd": "Capitalização mínima para alertar (vazio = sem mínimo)",
               "alertar_bateu_mas_cai": "Avisar também quando bate mas o preço não cumpre",
               "hora_resumo_manha": "Hora do calendário da manhã",
               "minutos_antes_abertura_resumo": "Antecedência do lembrete antes da abertura",
               "dias_calendario": "Dias cobertos pelo calendário",
               "max_empresas_por_dia_no_resumo": "Limite de empresas por dia no calendário",
               "guidance_no_alerta": "Incluir excerto do comunicado",
               "alerta_cedo_sec": "Alerta antecipado pela SEC (⚡)",
               "avisar_falhas_seguidas": "Passagens seguidas com erro até avisar",
               "ntfy_servidor": "Servidor ntfy"}
    rows = [["Parâmetro (config.json)", "Valor", "Significado"]]
    for k, v in cfg.items():
        val = "vazio" if v is None else (pt(v, 0) if isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) >= 1e6
                                          else str(v).lower() if isinstance(v, bool) else str(v))
        rows.append([code(k), val, meaning.get(k, "")])
    T(rows, [66, 32, 76])
    callout("O tópico ntfy e o email de contacto da SEC nunca estão no código: vêm dos segredos " + code("NTFY_TOPICO") + " e "
            + code("SEC_CONTACTO") + " no GitHub, ou de " + code("config.local.json") + " (fora do git) no PC.", ACC, "Segredos")

    story.append(Section(8, "Automação (GitHub Actions)"))
    T([["Workflow", "Disparo", "Comportamento"],
       ["<b>Bot Resultados</b><br/>" + code("bot.yml"), "Cron de hora a hora seg–sex e sábado de madrugada; manual",
        "Corrida contínua de ~65 min (bot.py auto de 2 em 2 min, commit de data/ quando muda) que no fim lança a seguinte."],
       ["<b>Vigia do bot</b><br/>" + code("vigia.yml"), "De 30 em 30 min, seg–sex", "Avisa se não houver corridas do bot há mais de 90 minutos."],
       ["<b>Teste histórico</b><br/>" + code("historico.yml"), "Dia 1 de cada mês; manual", "backtest.py e premarket.py; commit dos resultados."],
       ["<b>Publicar dashboard</b><br/>" + code("pagina.yml"), "Push que altere docs/", "Publica docs/ no GitHub Pages."],
       ["<b>Testes</b><br/>" + code("testes.yml"), "Push que altere código", "Corre o pytest."],
       ["<b>Diagnóstico do guidance</b>", "Manual, com símbolo", "Mostra o excerto que seria tirado do comunicado."]],
      [38, 46, 90])

    story.append(Section(9, "Dashboard"))
    P("Página única (" + code("docs/index.html") + ") que lê os ficheiros de data/ diretamente do repositório:")
    B(["Números da estratégia com os alertas reais: do alerta à abertura (média, mediana, quantos subiram), do alerta ao "
       "fecho, atraso do Yahoo e da SEC.",
       "Próximos relatórios, alertas positivos (⚡ nos antecipados) e todos os resultados registados.",
       "Teste histórico: grelha de critérios, critério do bot e candidatos, validação fora da amostra.",
       "Teste do pre-market: comprar antes da abertura e a tabela “Quando vender”."])

    story.append(Section(10, "Teste histórico (preços diários)"))
    P(f"{pt(hist['relatorios'], 0)} relatórios de {hist['acoes']} ações, de {fd(hist['periodo'][0])} a {fd(hist['periodo'][1])}, "
      "comprando na abertura do dia da reação. Mede cada critério face à média do mesmo trimestre e escolhe critérios numa "
      "metade do período para os medir na outra.")
    crits = hist.get("criterios", [])
    if crits:
        story.append(HBars([(f"{c['nome']}", c.get("media_ab_fecho") or 0) for c in crits]))
        story.append(Spacer(1, 6))
        rows = [["Critério", "Regra", "Casos", "Abertura → fecho", "Trimestres acima"]]
        rows += [[c["nome"], c["regra"], pt(c["n"], 0), tone(c.get("media_ab_fecho")), f"{c['trimestres_acima']} de {c['trimestres']}"]
                 for c in crits]
        T(rows, [30, 76, 18, 26, 24], aligns="LLRRR")
    callout("Com o critério atual, o ganho está antes da abertura: comprado na abertura rende pouco. Por isso a "
            "estratégia é comprar no alerta, e o teste que interessa é o do pre-market.", AMBER, "Leitura")

    story.append(Section(11, "Teste do pre-market (barras de 1 hora)"))
    P(f"{pt(pre['relatorios'], 0)} relatórios de {fd(pre['periodo'][0])} a {fd(pre['periodo'][1])}, com o critério do bot "
      f"({pre['criterio_bot']}) medido no momento da compra.")
    rows = [["Momento da compra", "Casos", "Até à abertura", "Até ao fecho", "Sobem até ao fecho", "Trimestres acima"]]
    for e in pre["entradas"].values():
        b = e["bot"]
        rows.append([e["nome"], pt(b["n"], 0), tone(b.get("media_abertura")), tone(b.get("media_fecho")),
                     pt(b.get("pct_sobe_ate_fecho"), 0, False, "%"), f"{b.get('trimestres_acima')} de {b.get('trimestres')}"])
    T(rows, [52, 18, 26, 26, 28, 24], aligns="LRRRRR")
    P("Limitação: fora da sessão o Yahoo não dá volume, por isso não se sabe se havia liquidez para comprar a esse preço; "
      "a diferença entre compra e venda no after-hours costuma ser maior do que na sessão.", small)

    story.append(Section(12, "Estado atual (dados ao vivo)"))
    P(f"Registo de {fd(d0)} a {fd(d1)}. Os alertas até 23/11/2026 seguiram critérios anteriores (surpresa ≥ 5% e subida ≥ 2%); "
      "os números do critério atual vão aparecendo à medida que saem alertas.")
    kpis([(str(len(reg)), "resultados avaliados", "#2a78d6"),
          (str(len(pos)), f"alertas positivos ({len(early)} ⚡)", "#006300"),
          (pt(st.mean([v for _, v in fa]), 2, True, "%") if fa else "–", f"do alerta à abertura ({len(fa)} alertas)",
           "#006300" if fa and st.mean([v for _, v in fa]) >= 0 else "#d03b3b"),
          (f"{st.median(delays):.0f} min" if delays else "–",
           "atraso do Yahoo" + (f" · SEC {st.median(sec_delays):.0f} min" if sec_delays else ""), "#b7791f")])
    if fa:
        H2("Do alerta à abertura, por alerta")
        story.append(VBars(fa[-40:]))

    story.append(Section(13, "Qualidade"))
    try:  # número de casos que o pytest corre
        import sys
        out_ = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=ROOT, capture_output=True, text=True).stdout
        n_tests = int(next(l for l in out_.splitlines() if "collected" in l or "tests collected" in l).split()[0])
    except Exception:
        n_tests = None
    P(f"{n_tests or 'Os'} testes automáticos cobrem: reação do preço e casos tardios; critérios do alerta; "
      "registo e migração de colunas; feed da SEC e leitura do EPS (texto e tabelas, incluindo o que deve ser ignorado); "
      "alerta antecipado, confirmação e correção; avisos de falhas e vigia; dicas de compra e venda, lembretes e dia da "
      "reação; testes históricos (resumos, validação, melhor saída). Correm no GitHub a cada mudança do código.")

    story.append(Section(14, "Limitações e riscos"))
    B(["Dados gratuitos e não oficiais (Yahoo, Nasdaq): atrasos e falhas pontuais. O Yahoo só publica o EPS horas depois.",
       "O alerta ⚡ só sai quando o EPS está claro no comunicado (cerca de metade) e pode ler um EPS de outra base (↩️).",
       "Liquidez no after-hours e no pre-market desconhecida (o Yahoo não dá volume fora da sessão); spreads maiores.",
       "A corretora tem de permitir negociar fora da sessão para seguir a estratégia.",
       "Testes com viés de sobrevivência (só ações atuais), muitas combinações testadas e custos aproximados.",
       "Euronext: cobertura fraca de estimativas e sem alerta antecipado.",
       "Resultados passados não garantem resultados futuros."])

    desc = {1: "O que o bot faz e a estratégia", 2: "Fontes, bot e saídas; componentes",
            3: "Avisos no telemóvel e comandos", 4: "Critério, alerta antecipado ⚡ e conteúdo",
            5: "Melhor hora de compra e de venda, com custos", 6: "Ficheiros em data/",
            7: "Parâmetros do config.json e segredos", 8: "Workflows e vigia",
            9: "Página pública no GitHub Pages", 10: "Preços diários, validação fora da amostra",
            11: "After-hours e pre-market, barras de 1 hora", 12: "Registo real e alertas",
            13: "Testes automáticos", 14: "O que o bot não garante"}
    story.insert(1, Index([(x.num, x.text) for x in story if isinstance(x, Section)], desc))
    story.insert(2, PageBreak())
    merged, i = [], 0
    while i < len(story):
        if isinstance(story[i], Section) and i + 1 < len(story) and isinstance(story[i + 1], KeepTogether):
            merged.append(KeepTogether([story[i]] + story[i + 1]._content))
            i += 2
        else:
            merged.append(story[i])
            i += 1
    for _ in range(2):  # 1.ª passagem descobre as páginas de cada secção, a 2.ª escreve-as no índice
        doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm,
                                bottomMargin=20 * mm, title="Bot de Resultados Trimestrais — Especificação",
                                author="Luís Pereira", subject="Especificação funcional e técnica")
        doc.build(copy.deepcopy(merged), onFirstPage=cover, onLaterPages=inner)
    print("ok", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", default=str(ROOT / "ESPECIFICACAO.pdf"))
    build(Path(ap.parse_args().saida))
