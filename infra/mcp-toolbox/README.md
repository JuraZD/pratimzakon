# MCP Toolbox — Upute za pokretanje

Read-only AI pristup PratimZakon PostgreSQL bazi. Interan servis, nije dio produkcijskog backenda.

## Preduvjeti

- Go 1.21+ ili Docker (za pokretanje toolbox binarija)
- Pristup PostgreSQL bazi (Render.com ili lokalna instanca)
- Read-only DB korisnik (vidi korak 1 ispod)

## Korak 1: Kreiraj read-only DB korisnika

Poveži se na PostgreSQL (Render dashboard → Connect → External connection) i izvršo:

```sql
-- Kreiraj korisnika
CREATE USER mcp_readonly WITH PASSWORD 'odaberi_jaku_lozinku';

-- Dozvoli spajanje na bazu
GRANT CONNECT ON DATABASE pratimzakon TO mcp_readonly;

-- Dozvoli čitanje sheme
GRANT USAGE ON SCHEMA public TO mcp_readonly;

-- Dozvoli SELECT samo na potrebnim tablicama
GRANT SELECT ON TABLE
  users,
  keywords,
  keyword_groups,
  documents,
  logs,
  user_settings,
  push_subscriptions
TO mcp_readonly;

-- Onemogući budući automatski pristup novim tablicama
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE ALL ON TABLES FROM mcp_readonly;
```

Provjeri ručno:
```bash
psql "postgresql://mcp_readonly:lozinka@host:5432/pratimzakon" -c "\dt"
```

## Korak 2: Postavi environment varijable

```bash
cp infra/mcp-toolbox/.env.template infra/mcp-toolbox/.env
# Uredi .env s podacima za mcp_readonly korisnika
```

Za Render.com External connection string format:
```
PGHOST=dpg-xxxx.oregon-postgres.render.com
PGPORT=5432
PGDATABASE=pratimzakon
PGUSER=mcp_readonly
PGPASSWORD=tvoja_lozinka
```

## Korak 3: Pokretanje

### Opcija A — Go binary (preporučeno)

```bash
# Preuzmi MCP Toolbox
go install github.com/googleapis/genai-toolbox@latest

# Pokretanje
cd infra/mcp-toolbox
source .env
toolbox --tools-file tools.yaml --port $MCP_PORT
```

### Opcija B — Docker

```bash
cd infra/mcp-toolbox
docker run --rm \
  --env-file .env \
  -v $(pwd)/tools.yaml:/app/tools.yaml \
  -p 5000:5000 \
  us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:latest \
  --tools-file /app/tools.yaml --port 5000
```

### Opcija C — npx (eksperimentalno)

```bash
npx @googleapis/mcp-toolbox --tools-file infra/mcp-toolbox/tools.yaml
```

## Korak 4: Spoji MCP klijent

Vidi `mcp.json.example` za primjer konfiguracije. Toolset koji koristiš zove se `ops_readonly`.

Za provjeru da toolbox radi:
```bash
curl http://localhost:5000/api/toolset/ops_readonly
```

## Troubleshooting

| Problem | Rješenje |
|---------|----------|
| `connection refused` | Provjeri PGHOST/PGPORT, je li Render External connection aktivan |
| `permission denied for table X` | Provjeri GRANT naredbe iz Koraka 1 |
| `password authentication failed` | Provjeri PGUSER/PGPASSWORD u .env |
| Toolbox ne vidi tools.yaml | Provjeri da pokrećeš iz pravog direktorija ili koristi apsolutni path |
| MCP klijent ne vidi toolset | Provjeri da port u mcp.json odgovara `--port` argumentu |

## Sigurnosne napomene

- **Produkcijska baza mora ostati read-only.** `mcp_readonly` korisnik nikad ne smije imati `INSERT`, `UPDATE` ili `DELETE`.
- Ne commitaj `.env` s pravim credentialima.
- Ne izlažu se `password_hash`, `unsubscribe_token`, `p256dh`, `auth` stupci — isključeni su iz SQL upita u tools.yaml.
- MCP Toolbox ne smije biti dostupan s interneta — pokreći ga lokalno ili unutar privatne mreže.
