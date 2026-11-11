# Cria uma tarefa no Agendador do Windows que corre o bot a cada 15 minutos.
# O bot decide sozinho o que fazer consoante a hora (resumo da manhã, verificação, resumo pre-market).
# Para remover:  Unregister-ScheduledTask -TaskName "Bot Resultados" -Confirm:$false

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw  = Join-Path $root ".venv\Scripts\pythonw.exe"   # pythonw = sem janela a piscar
$bot  = Join-Path $root "bot.py"

$action  = New-ScheduledTaskAction -Execute $pyw -Argument "`"$bot`" auto" -WorkingDirectory $root
$trigger = @(
    (New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 15)),
    (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME")   # corre também logo ao iniciar sessão
)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 14) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "Bot Resultados" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Avisos de resultados trimestrais (ntfy)" -Force | Out-Null

Write-Host "Tarefa 'Bot Resultados' criada: corre a cada 15 minutos enquanto o PC estiver ligado."
