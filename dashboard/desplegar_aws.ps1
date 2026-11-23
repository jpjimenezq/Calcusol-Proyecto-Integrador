<#
.SYNOPSIS
  Publica el dashboard en AWS (S3 + CloudFront).

.DESCRIPTION
  Copia dashboard/dashboard a una carpeta temporal, escribe en ella js/config.js con la dirección HTTPS de la API,
  sube todo al bucket y pide a CloudFront que olvide la versión anterior.
  Antes, si cambiaron los datos:  python dashboard/pipeline/build_data_aws.py

.EXAMPLE
  .\dashboard\desplegar_aws.ps1 -ApiUrl https://ca-660aa7a821e841e6944d63c7643ff905.ecs.us-east-1.on.aws
#>
param(
  [Parameter(Mandatory = $true)][string]$ApiUrl,
  [string]$Perfil = 'calcusol-despliegue',
  [string]$Region = 'us-east-1',
  [string]$Bucket = 'calcusol-dashboard-607581913067',
  [string]$Distribucion = 'E2ZC5DM55MEH63'
)

$ErrorActionPreference = 'Stop'
$origen = Join-Path $PSScriptRoot 'dashboard'
if (-not (Test-Path (Join-Path $origen 'data/colombia.json'))) {
  throw 'Falta dashboard/dashboard/data/colombia.json. Genérelo con build_data_aws.py (o build_demo_data.py).'
}
$aws = @('--profile', $Perfil, '--region', $Region)

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('calcusol-dashboard-' + [guid]::NewGuid().ToString('N'))
Copy-Item $origen $tmp -Recurse
try {
  Set-Content -Path (Join-Path $tmp 'js/config.js') -Encoding utf8 -Value "window.CALCUSOL_API = '$($ApiUrl.TrimEnd('/'))';"

  # Código y estilos: caché corta, para que un cambio llegue pronto. --delete quita del bucket lo que ya no existe.
  aws s3 sync $tmp "s3://$Bucket" @aws --delete --exclude 'data/*' --exclude 'index.html' --exclude 'js/config.js' --cache-control 'public, max-age=300'
  # Página, configuración y datos: el navegador pregunta siempre si hay una versión nueva (y solo descarga si cambió),
  # así una actualización llega a todos de inmediato y no queda nadie con datos viejos guardados.
  aws s3 cp (Join-Path $tmp 'index.html') "s3://$Bucket/index.html" @aws --cache-control 'no-cache' --content-type 'text/html; charset=utf-8'
  aws s3 cp (Join-Path $tmp 'js/config.js') "s3://$Bucket/js/config.js" @aws --cache-control 'no-cache' --content-type 'application/javascript; charset=utf-8'
  aws s3 cp (Join-Path $tmp 'data/colombia.json') "s3://$Bucket/data/colombia.json" @aws --cache-control 'no-cache' --content-type 'application/json; charset=utf-8'

  aws cloudfront create-invalidation @aws --distribution-id $Distribucion --paths '/*' --query 'Invalidation.Status' --output text
  $dominio = aws cloudfront get-distribution @aws --id $Distribucion --query 'Distribution.DomainName' --output text
  Write-Host "Publicado: https://$dominio"
}
finally {
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
