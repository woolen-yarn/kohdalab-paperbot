param(
    [string]$ModelRoot = "C:\ProgramData\PaperBot",
    [string]$GenerationModel = "gemma-4-26B-A4B-it-IQ4_XS.gguf",
    [string]$ServerPath = "C:\ProgramData\PaperBot\bin\llama-server.exe"
)

$ErrorActionPreference = "Stop"
$server = $ServerPath
if (-not (Test-Path $server)) {
    throw "llama-server.exe was not found at $server"
}
$logRoot = Join-Path $ModelRoot "logs"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force

$generationArgs = @(
    "-m", (Join-Path $ModelRoot $GenerationModel),
    "--host", "0.0.0.0", "--port", "8080",
    "-c", "16384", "-np", "1",
    "--fit", "on", "--fit-target", "1024", "--fit-ctx", "16384",
    "--flash-attn", "on", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
    "--jinja", "--no-webui"
)
$generation = Start-Process -FilePath $server -ArgumentList $generationArgs -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logRoot "generation.stdout.log") `
    -RedirectStandardError (Join-Path $logRoot "generation.stderr.log")

$embeddingArgs = @(
    "-m", (Join-Path $ModelRoot "Qwen3-Embedding-4B-Q8_0.gguf"),
    "--host", "0.0.0.0", "--port", "8081",
    "-c", "8192", "-np", "1", "-ngl", "0",
    "--embedding", "--no-webui"
)
$embedding = Start-Process -FilePath $server -ArgumentList $embeddingArgs -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logRoot "embedding.stdout.log") `
    -RedirectStandardError (Join-Path $logRoot "embedding.stderr.log")

Write-Output "Started PaperBot generation on :8080 and CPU embeddings on :8081"
while (-not $generation.HasExited -and -not $embedding.HasExited) {
    Start-Sleep -Seconds 5
    $generation.Refresh()
    $embedding.Refresh()
}
Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
throw "A PaperBot llama.cpp server exited; Task Scheduler will restart both servers."
