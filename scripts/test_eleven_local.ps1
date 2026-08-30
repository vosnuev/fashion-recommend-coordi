[CmdletBinding()]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$Keyword = "$([char]0xBC18)$([char]0xD314) $([char]0xD2F0)$([char]0xC154)$([char]0xCE20)",

    [Parameter()]
    [ValidateRange(1, 10)]
    [int]$Limit = 1
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$composeFile = "docker-compose.eleven-test.yml"

function Invoke-TestCompose {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    & docker compose -f $composeFile @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose -f $composeFile $($Arguments -join ' ') failed."
    }
}

function Get-DbSetting {
    param(
        [Parameter(Mandatory)]
        [ValidateSet("POSTGRES_USER", "POSTGRES_DB")]
        [string]$Name
    )

    $value = (& docker compose -f $composeFile exec -T db printenv $Name | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $value) {
        throw "Test database setting $Name is unavailable."
    }
    return $value
}

function Invoke-DbScalar {
    param(
        [Parameter(Mandatory)]
        [string]$Sql
    )

    $dbUser = Get-DbSetting -Name "POSTGRES_USER"
    $dbName = Get-DbSetting -Name "POSTGRES_DB"
    $output = & docker compose -f $composeFile exec -T db `
        psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName -tAc $Sql
    if ($LASTEXITCODE -ne 0) {
        throw "Database validation query failed."
    }
    return ($output | Out-String).Trim()
}

function Show-TestSummary {
    $sql = @"
SELECT
    eleven_product_id,
    left(title, 50) AS title,
    category_large,
    category_small,
    category_source,
    tagging_status,
    search_keyword,
    (image_url IS NOT NULL) AS has_image,
    collected_at
FROM eleven_product
ORDER BY updated_at DESC
LIMIT 5;
"@
    $dbUser = Get-DbSetting -Name "POSTGRES_USER"
    $dbName = Get-DbSetting -Name "POSTGRES_DB"
    & docker compose -f $composeFile exec -T db `
        psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName -c $sql
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read recently collected products."
    }
}

Push-Location $repoRoot
try {
    if (-not (Test-Path -LiteralPath ".env")) {
        throw "Root .env file is missing."
    }
    if (-not (Test-Path -LiteralPath $composeFile)) {
        throw "$composeFile is missing."
    }

    & docker version --format "Docker server {{.Server.Version}}" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker engine is unavailable. Start Docker Desktop first."
    }

    Write-Host "[1/7] Build isolated test services"
    Invoke-TestCompose -Arguments @("build", "migrate", "eleven-collector")
    Invoke-TestCompose -Arguments @("up", "-d", "--wait", "db")

    Write-Host "[2/7] Apply Django migrations to fashion_eleven_test"
    Invoke-TestCompose -Arguments @(
        "run", "--rm", "--no-deps", "migrate"
    )

    Write-Host "[3/7] Run XML parser and category mapping unit tests"
    Invoke-TestCompose -Arguments @(
        "run", "--rm", "--no-deps", "eleven-collector",
        "python", "-m", "unittest", "discover",
        "-s", "/app/eleven/tests", "-p", "test_*.py", "-v"
    )

    $beforeCategoryResponseId = [long](Invoke-DbScalar -Sql(
        "SELECT COALESCE(MAX(id), 0) FROM eleven_api_response;"
    ))

    Write-Host "[4/7] Synchronize the real 11st category API response"
    Invoke-TestCompose -Arguments @(
        "run", "--rm", "--no-deps", "eleven-collector",
        "python", "/app/eleven/eleven_collector_db.py",
        "--job", "sync-categories"
    )

    $successfulCategoryResponses = [long](Invoke-DbScalar -Sql(
        "SELECT COUNT(*) FROM eleven_api_response " +
        "WHERE id > $beforeCategoryResponseId " +
        "AND api_name = 'category' " +
        "AND response_status BETWEEN 200 AND 299 " +
        "AND error_message IS NULL;"
    ))
    $categoryCount = [long](Invoke-DbScalar -Sql(
        "SELECT COUNT(*) FROM eleven_category WHERE is_active = TRUE;"
    ))
    if ($successfulCategoryResponses -lt 1 -or $categoryCount -lt 1) {
        throw "The category API did not produce active eleven_category rows."
    }

    $beforeProductResponseId = [long](Invoke-DbScalar -Sql(
        "SELECT COALESCE(MAX(id), 0) FROM eleven_api_response;"
    ))

    Write-Host "[5/7] Collect 11st products without LLM tagging"
    Invoke-TestCompose -Arguments @(
        "run", "--rm", "--no-deps", "eleven-collector",
        "python", "/app/eleven/eleven_collector_db.py",
        "--job", "collect",
        "--keyword", $Keyword,
        "--limit", "$Limit",
        "--skip-llm"
    )

    Write-Host "[6/7] Validate API response and database rows"
    $successfulProductResponses = [long](Invoke-DbScalar -Sql(
        "SELECT COUNT(*) FROM eleven_api_response " +
        "WHERE id > $beforeProductResponseId " +
        "AND api_name = 'product_search' " +
        "AND response_status BETWEEN 200 AND 299 " +
        "AND error_message IS NULL;"
    ))
    $savedProducts = [long](Invoke-DbScalar -Sql(
        "SELECT COUNT(*) FROM eleven_product " +
        "WHERE api_response_id > $beforeProductResponseId;"
    ))
    $pendingProducts = [long](Invoke-DbScalar -Sql(
        "SELECT COUNT(*) FROM eleven_product " +
        "WHERE api_response_id > $beforeProductResponseId " +
        "AND tagging_status = 'pending' " +
        "AND tagged_at IS NULL;"
    ))

    if ($successfulProductResponses -lt 1) {
        throw "No successful ProductSearch response was stored."
    }
    if ($savedProducts -lt 1) {
        throw "No product row references the new ProductSearch response."
    }
    if ($pendingProducts -ne $savedProducts) {
        throw "Unexpected tagging state: LLM tagging must remain disabled."
    }

    Write-Host "[7/7] Smoke test passed"
    Write-Host "Active categories: $categoryCount"
    Write-Host "Successful ProductSearch responses: $successfulProductResponses"
    Write-Host "Saved or updated products: $savedProducts"
    Write-Host "Products left untagged: $pendingProducts"
    Show-TestSummary
    Write-Host "The isolated local database remains running on port 15432."
} finally {
    Pop-Location
}
