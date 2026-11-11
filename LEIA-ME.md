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

## Dashboard

https://luispereira1999.github.io/bot-resultados/ mostra os próximos relatórios, os alertas enviados e o que cada ação fez a seguir. A página (`docs/index.html`) lê `data/registo.csv` e `data/calendario.json` diretamente do repositório, por isso atualiza-se sozinha. O bot preenche a reação de cada ação todas as noites, depois do fecho de Nova Iorque.

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

O bot corre na nuvem com o GitHub Actions (`.github/workflows/bot.yml`), em verificação contínua de segunda a sexta (de 2 em 2 minutos), mesmo com o PC desligado. A coluna `atraso_min` do registo mostra quantos minutos passaram entre a hora marcada do relatório e o bot o ver publicado no Yahoo. O tópico ntfy está no segredo `NTFY_TOPICO` do repositório. O estado e o registo são guardados de volta na pasta `data/` a cada corrida.

- Ver as corridas: separador **Actions** do repositório.
- Pausar: Actions → Bot Resultados → ⋯ → *Disable workflow*.
- Ter o registo atualizado no PC: fazer *Pull* (GitHub Desktop) antes de abrir `data/registo.csv` ou correr `rever`.

Alternativa sem nuvem: `agendar.ps1` cria uma tarefa no Windows que corre o bot a cada 15 min, só com o PC ligado. Não uses os dois ao mesmo tempo, senão recebes avisos em duplicado.

## Teste histórico

`backtest.py` mede o que as ações fizeram depois dos resultados dos últimos 2 anos e grava `data/historico_resumo.json` (mostrado na dashboard) e `data/historico.csv` (todos os casos). Corre na nuvem no dia 1 de cada mês, ou à mão em Actions → Teste histórico → *Run workflow*.

## Excerto do guidance

`guidance.py` vai ao comunicado na SEC e copia as frases de previsão para o alerta. A SEC exige um email de contacto real em cada pedido e não aceita o endereço noreply do GitHub. O email fica no segredo `SEC_CONTACTO` do repositório (e em `config.local.json` no PC), nunca no código. Testar em Actions → Diagnóstico do guidance.

## Testes

```
.venv\Scripts\python.exe -m pytest
```
Correm sozinhos no GitHub sempre que o código muda.

## Limitações

- Dados do Yahoo Finance e da Nasdaq (grátis, não oficiais): podem atrasar minutos ou falhar pontualmente.
- Receitas reais e guidance não vêm nos dados grátis: o aviso mostra as expectativas dos analistas e o link do comunicado.
- Euronext: muitas empresas só reportam de 6 em 6 meses e a cobertura de estimativas é menor.
- Bater as estimativas não garante subida. Usa `bot.py rever` para veres com dados se a estratégia funciona antes de arriscar dinheiro.
