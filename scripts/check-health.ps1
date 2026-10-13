param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
if (-not $env:QUANTLAB_API_OPERATOR_TOKEN -and -not $env:QUANTLAB_API_READER_TOKEN) { throw 'Inject a reader or operator credential.' }
$healthToken = $env:QUANTLAB_API_READER_TOKEN
if (-not $healthToken) { $healthToken = $env:QUANTLAB_API_OPERATOR_TOKEN }
$headers = @{ Authorization = "Bearer $healthToken" }
try {
    $baseUri = "http://127.0.0.1:$Port"
    $null = Invoke-RestMethod "$baseUri/api/v1/health"
    $null = Invoke-RestMethod "$baseUri/api/v1/ready" -Headers $headers
    $null = Invoke-RestMethod "$baseUri/openapi.json" -Headers $headers
    $page = Invoke-WebRequest "$baseUri/" -UseBasicParsing
    if ($page.StatusCode -ne 200 -or $page.Content -notmatch 'id="root"') { throw 'Dashboard missing' }
    Write-Output 'API, authenticated readiness, contract and production dashboard are available.'
} catch { throw 'Local health check failed; check process, port, credential, dashboard build and journal recovery.' }
