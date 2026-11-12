$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$history = @()

while ($true) {
    $question = Read-Host "Toi (tape quit pour sortir)"
    if ($question.Trim().Equals("quit", [StringComparison]::OrdinalIgnoreCase)) {
        break
    }
    if ([string]::IsNullOrWhiteSpace($question)) {
        continue
    }

    $body = @{
        question = $question
        top_k = 4
        history = $history
    } | ConvertTo-Json -Depth 10
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)

    try {
        $httpResponse = Invoke-WebRequest `
            -Uri "http://127.0.0.1:8001/ask" `
            -Method Post `
            -ContentType "application/json; charset=utf-8" `
            -Body $bodyBytes `
            -UseBasicParsing

        $stream = $httpResponse.RawContentStream
        $stream.Position = 0
        $reader = New-Object System.IO.StreamReader($stream, $utf8)
        $response = $reader.ReadToEnd() | ConvertFrom-Json
        $reader.Dispose()

        Write-Host "`nQwen : $($response.answer)`n"

        $history += @{ role = "user"; content = $question }
        $history += @{ role = "assistant"; content = $response.answer }
        if ($history.Count -gt 24) {
            $history = @($history | Select-Object -Last 24)
        }
    }
    catch {
        $errorResponse = $_.Exception.Response
        if ($errorResponse) {
            $statusCode = [int]$errorResponse.StatusCode
            $statusDescription = $errorResponse.StatusDescription
            $responseStream = $errorResponse.GetResponseStream()
            $errorBody = ""
            if ($responseStream) {
                $errorReader = New-Object System.IO.StreamReader($responseStream, $utf8)
                $errorBody = $errorReader.ReadToEnd()
                $errorReader.Dispose()
            }
            Write-Host "Erreur API HTTP $statusCode $statusDescription : $errorBody" -ForegroundColor Red
        }
        else {
            Write-Host "Erreur API : $($_.Exception.Message)" -ForegroundColor Red
        }
    }
}
