"""Vigia do bot: avisa no telemóvel se o bot de resultados deixar de correr nos dias úteis.

O bot avisa sozinho quando tem erros, mas não consegue avisar se nem sequer arrancar (por exemplo, o
GitHub deixar de lançar as corridas). Este script corre num workflow à parte (vigia.yml), lê as últimas
corridas do bot com o `gh` e manda um aviso ntfy se não houver nenhuma há mais de 90 minutos.
Só usa a biblioteca padrão, para arrancar depressa.

Uso (no GitHub Actions):  python vigia.py     precisa de GH_TOKEN, GH_REPO e NTFY_TOPICO
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ACTIVE = {"in_progress", "queued", "waiting", "pending", "requested"}
LIMIT_MIN = 90
REMINDER_HOURS_UTC = (7, 13)  # se continuar parado, lembra duas vezes por dia


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def minutes_idle(runs: list[dict], now: datetime) -> float | None:
    """Minutos sem corridas do bot; None se há uma a correr ou na fila.

    À segunda-feira conta só a partir das 00:05 UTC, quando o bot deve arrancar depois do fim de semana.
    """
    if any(r.get("status") in ACTIVE for r in runs):
        return None
    last = max((parse(r["updatedAt"]) for r in runs), default=now - timedelta(days=7))
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=5, second=0, microsecond=0)
    return (now - max(last, week_start)).total_seconds() / 60


def should_alert(idle: float | None, now: datetime) -> bool:
    """Um aviso quando passa o limite (o vigia corre de 30 em 30 min) e lembretes às 07:00 e 13:00 UTC."""
    if idle is None or idle < LIMIT_MIN or now.weekday() >= 5:
        return False
    return idle < LIMIT_MIN + 30 or (now.hour in REMINDER_HOURS_UTC and now.minute < 30)


def main() -> None:
    now = datetime.now(timezone.utc)
    runs = json.loads(subprocess.check_output(
        ["gh", "run", "list", "--workflow", "bot.yml", "--limit", "10", "--json", "status,createdAt,updatedAt"]))
    idle = minutes_idle(runs, now)
    print("bot a correr" if idle is None else f"sem corridas do bot há {idle:.0f} min")
    if not should_alert(idle, now):
        return
    cfg = json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))
    body = {"topic": os.environ["NTFY_TOPICO"].strip(), "priority": 4, "tags": ["warning"],
            "title": "⚠️ O bot de resultados parou",
            "message": f"Nenhuma corrida há {idle / 60:.1f} h. Em GitHub → Actions → Bot Resultados, carrega em "
                       "Run workflow para o relançar."}
    headers = {"Content-Type": "application/json"}
    if os.environ.get("NTFY_TOKEN", "").strip():  # opcional: tópico protegido por token
        headers["Authorization"] = f"Bearer {os.environ['NTFY_TOKEN'].strip()}"
    req = urllib.request.Request(cfg.get("ntfy_servidor", "https://ntfy.sh").rstrip("/"), data=json.dumps(body).encode(),
                                 headers=headers)
    urllib.request.urlopen(req, timeout=20)
    print("aviso enviado")


if __name__ == "__main__":
    sys.exit(main())
