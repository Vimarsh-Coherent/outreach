# Creates the outreach role + coherent_outreach database, then runs alembic migrations.
# Run AFTER switching local Postgres to trust auth on 127.0.0.1/32 + ::1/128.

$ErrorActionPreference = "Stop"
$psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"

if (-not (Test-Path $psql)) {
    Write-Error "psql.exe not found at $psql. Update the path in this script."
}

# Local-dev credentials. Change these if you rotate the postgres password.
$env:PGPASSWORD = 'Vimarsh@121'
$appRolePassword = 'OutreachLocal2026'

Write-Host "==> Creating role + database..." -ForegroundColor Cyan
& $psql -U postgres -h localhost -d postgres -v ON_ERROR_STOP=1 -c @"
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='outreach') THEN
    CREATE ROLE outreach LOGIN PASSWORD '$appRolePassword';
  END IF;
END
`$`$;
"@

# CREATE DATABASE cannot run inside a DO block.
$dbExists = & $psql -U postgres -h localhost -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='coherent_outreach'"
if ($dbExists -ne "1") {
    & $psql -U postgres -h localhost -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE coherent_outreach OWNER outreach"
} else {
    Write-Host "    database coherent_outreach already exists" -ForegroundColor Yellow
}

& $psql -U postgres -h localhost -d coherent_outreach -v ON_ERROR_STOP=1 -c @"
GRANT ALL PRIVILEGES ON DATABASE coherent_outreach TO outreach;
GRANT ALL ON SCHEMA public TO outreach;
"@

Write-Host "==> Running alembic upgrade head..." -ForegroundColor Cyan
Push-Location (Join-Path $PSScriptRoot "..\backend")
try {
    & alembic upgrade head
} finally {
    Pop-Location
}

Write-Host "==> Done." -ForegroundColor Green
