# Bot de resultados trimestrais

Avisa no telemóvel (app **ntfy**) sobre os relatórios de resultados de ações da NASDAQ, NYSE e Euronext.

## O que recebes

| Quando (Lisboa) | Aviso |
|---|---|
| 08:30, seg–sex | Calendário dos próximos 3 dias: empresas, hora (🌅 antes da abertura / 🌙 depois do fecho) e EPS estimado |
| Assim que sai um resultado | 🟢 só se for **positivo**: EPS acima da estimativa (≥ 5%) **e** preço a subir (≥ 2%) no pre-market/after-hours. Inclui expectativas dos analistas para o próximo trimestre, revisões recentes e link para o comunicado (secção *Outlook* = guidance) |
| 30 min antes da abertura de Nova Iorque (normalmente 14:00) | Resumo dos positivos com o movimento atualizado |

Resultados que o bot só apanha depois de a sessão de reação acabar (ex.: PC desligado) ficam no registo como `tardio` e não geram aviso.

Todos os resultados (positivos ou não) ficam em `data/registo.csv`. Ao sábado o bot preenche o que cada ação fez no dia seguinte ao relatório.

## Instalar a app ntfy

1. Instala **ntfy** (Android: Play Store / iPhone: App Store).
2. Carrega em **+** e subscreve o tópico que está em `config.local.json` → `ntfy_topico` (este ficheiro fica só no PC).
3. Mantém o nome do tópico privado: quem o souber consegue ler os avisos.

## Comandos

```
.venv\Scripts\python.exe bot.py teste       # notificação de teste
.venv\Scripts\python.exe bot.py manha       # calendário agora
.venv\Scripts\python.exe bot.py verificar   # procura resultados novos
.venv\Scripts\python.exe bot.py rever       # o que as ações fizeram depois dos resultados
```
Acrescenta `--seco` para ver no ecrã sem enviar.

## Ajustar

- `config.json`: limites (surpresa mínima, subida mínima, capitalização mínima), horas dos resumos.
- `euronext.txt`: lista de ações europeias a seguir (símbolos do Yahoo, ex.: `GALP.LS`, `ASML.AS`).

## Automático

O bot corre na nuvem com o GitHub Actions (`.github/workflows/bot.yml`), a cada 15 minutos de segunda a sexta, mesmo com o PC desligado. O tópico ntfy está no segredo `NTFY_TOPICO` do repositório. O estado e o registo são guardados de volta na pasta `data/` a cada corrida.

- Ver as corridas: separador **Actions** do repositório.
- Pausar: Actions → Bot Resultados → ⋯ → *Disable workflow*.
- Ter o registo atualizado no PC: fazer *Pull* (GitHub Desktop) antes de abrir `data/registo.csv` ou correr `rever`.

Alternativa sem nuvem: `agendar.ps1` cria uma tarefa no Windows que corre o bot a cada 15 min, só com o PC ligado. Não uses os dois ao mesmo tempo, senão recebes avisos em duplicado.

## Limitações

- Dados do Yahoo Finance e da Nasdaq (grátis, não oficiais): podem atrasar minutos ou falhar pontualmente.
- Receitas reais e guidance não vêm nos dados grátis: o aviso mostra as expectativas dos analistas e o link do comunicado.
- Euronext: muitas empresas só reportam de 6 em 6 meses e a cobertura de estimativas é menor.
- Bater as estimativas não garante subida. Usa `bot.py rever` para veres com dados se a estratégia funciona antes de arriscar dinheiro.
