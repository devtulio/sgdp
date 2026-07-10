# Cria atalho "Iniciar SGDP.lnk" na Area de Trabalho com icone personalizado
$batPath  = Join-Path $PSScriptRoot "Iniciar SGDP.bat"
$icoPath  = Join-Path $PSScriptRoot "sgdp.ico"
$desktop  = [Environment]::GetFolderPath("Desktop")
$lnkPath  = Join-Path $desktop "Iniciar SGDP.lnk"

$wsh  = New-Object -ComObject WScript.Shell
$link = $wsh.CreateShortcut($lnkPath)
$link.TargetPath       = $batPath
$link.IconLocation     = "$icoPath,0"
$link.WorkingDirectory = $PSScriptRoot
$link.WindowStyle      = 7
$link.Description      = "SGDP - Sistema de Gestao de Documentos da Procuradoria"
$link.Save()

Write-Host "Atalho criado em: $lnkPath" -ForegroundColor Green
