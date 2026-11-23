<#
Despliega en AWS el reentrenamiento del modelo de radiacion (fase C). Por defecto NO cambia nada:
muestra cada comando. Para ejecutarlos use -Aplicar. Cada paso es idempotente o falla sin danar lo existente.

  .\infra\mlops\desplegar_mlops.ps1                 # solo muestra
  .\infra\mlops\desplegar_mlops.ps1 -Aplicar        # ejecuta los pasos 1 a 6 (imagen, tarea, v1 en S3, pipeline)
  .\infra\mlops\desplegar_mlops.ps1 -Aplicar -ConRegla   # ademas crea la regla mensual (queda DESHABILITADA)

Requisitos: Docker abierto, perfil calcusol-despliegue, politica calcusol-mlops y roles
calcusol-entrenamiento-tarea y calcusol-stepfunctions-pipeline (con permisos de ECS) ya creados.
La regla mensual necesita ademas el rol calcusol-eventbridge-pipeline (ver docs/mlops.md).
#>
param([switch]$Aplicar, [switch]$ConRegla, [string]$Etiqueta = (Get-Date -Format "yyyy-MM-dd"))

$ErrorActionPreference = "Stop"
$env:MSYS_NO_PATHCONV = "1"
$perfil = @("--profile", "calcusol-despliegue", "--region", "us-east-1")
$cuenta = "607581913067"
$repo = "$cuenta.dkr.ecr.us-east-1.amazonaws.com/calcusol-entrenamiento"
$maquina = "arn:aws:states:us-east-1:${cuenta}:stateMachine:calcusol-stepfunctions-pipeline"
$raiz = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $raiz

function Paso($texto, [scriptblock]$accion) {
    Write-Host "`n== $texto" -ForegroundColor Cyan
    if ($Aplicar) { & $accion } else { Write-Host ($accion.ToString().Trim()) -ForegroundColor DarkGray }
}

Paso "1. Grupo de logs y repositorio ECR" {
    aws logs create-log-group --log-group-name /calcusol/entrenamiento @perfil 2>$null
    aws logs put-retention-policy --log-group-name /calcusol/entrenamiento --retention-in-days 30 @perfil
    aws ecr create-repository --repository-name calcusol-entrenamiento --image-scanning-configuration scanOnPush=true @perfil 2>$null
}

Paso "2. Construir y subir la imagen ($Etiqueta)" {
    $commit = (git rev-parse --short HEAD)
    docker build -f infra/mlops/Dockerfile.entrenamiento --build-arg GIT_COMMIT=$commit -t "${repo}:$Etiqueta" .
    aws ecr get-login-password @perfil | docker login --username AWS --password-stdin "$cuenta.dkr.ecr.us-east-1.amazonaws.com"
    docker push "${repo}:$Etiqueta"
}

Paso "3. Registrar la definicion de tarea (Fargate 4 vCPU / 16 GB)" {
    $json = (Get-Content infra/mlops/tarea_entrenamiento.json -Raw).Replace("IMAGEN_TAG", $Etiqueta)
    $tmp = Join-Path $env:TEMP "tarea_entrenamiento.json"
    [IO.File]::WriteAllText($tmp, $json)
    aws ecs register-task-definition --cli-input-json "file://$tmp" @perfil --query "taskDefinition.taskDefinitionArn"
}

Paso "4. Subir v1 al registro de S3 (solo si no existe; no se sobrescribe). Requiere Python con lightgbm, pandas, s3fs (pip install -r infra/mlops/requirements-entrenamiento.txt)" {
    $env:AWS_PROFILE = "calcusol-despliegue"; $env:PYTHONPATH = "src"
    python -m calcusol.ml.promocion sembrar --version v1 --origen models/radiacion/v1 --registro s3://calcusol-datos-eafit/modelos
}

Paso "5. Actualizar la maquina de estados (reentrenamiento + Fail + aviso de exito)" {
    aws stepfunctions update-state-machine --state-machine-arn $maquina --definition file://src/calcusol/stepfunctions/pipeline.json @perfil
}

if ($ConRegla) {
    Paso "6. Regla mensual (dia 1, 03:00 hora de Colombia = 08:00 UTC), DESHABILITADA hasta que usted la active" {
        aws events put-rule --name calcusol-pipeline-mensual --schedule-expression "cron(0 8 1 * ? *)" --state DISABLED --description "Pipeline Calcusol mensual (datos nuevos y reentrenamiento)" @perfil
        $destino = "[{`"Id`":`"pipeline`",`"Arn`":`"$maquina`",`"RoleArn`":`"arn:aws:iam::${cuenta}:role/calcusol-eventbridge-pipeline`",`"Input`":`"{}`"}]"
        $tmpDestino = Join-Path $env:TEMP "destino_regla.json"
        [IO.File]::WriteAllText($tmpDestino, $destino)
        aws events put-targets --rule calcusol-pipeline-mensual --targets "file://$tmpDestino" @perfil
    }
    Write-Host "`nPara activarla: aws events enable-rule --name calcusol-pipeline-mensual --profile calcusol-despliegue --region us-east-1"
}

if (-not $Aplicar) { Write-Host "`nNada se ejecuto. Use -Aplicar para hacerlo." -ForegroundColor Yellow }
