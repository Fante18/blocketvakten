# Deploya Blocketvakten till molnet

Den här guiden visar hur du flyttar Blocketvakten från din lokala dator
till Railway (molnplattform) med PostgreSQL, så att appen körs 24/7 och
alla konton, bevakningar och annonser sparas permanent.

## Översikt

```
Före (lokalt):                   Efter (moln):
┌──────────────────┐              ┌─────────────────────┐
│  Din dator       │              │  Railway (moln)     │
│  ┌─────────────┐ │              │  ┌────────────────┐ │
│  │ Python-app   │ │              │  │ Python-app     │ │
│  │ + SQLite     │─┼── stäng av ─▶│  │ + PostgreSQL   │ │
│  │ + schemal.   │ │    datorn    │  │ + schemal. 24/7│ │
│  └─────────────┘ │              │  └────────────────┘ │
└──────────────────┘              └─────────────────────┘
```

## Steg 1 – Skapa Railway-konto

1. Gå till [railway.app](https://railway.app)
2. Klicka **Start a New Project** → **Deploy from GitHub repo**
3. Välj ditt repo (du måste ha pushat filerna till GitHub först)
4. Railway upptäcker automatiskt Python-appen via `Procfile`

> **Alternativ:** Render ([render.com](https://render.com)) och Fly.io fungerar
> också. Samma filer (`requirements.txt`, `Procfile`) används.

## Steg 2 – Koppla PostgreSQL

1. I Railway-dashboarden, klicka på **+ New** → **Database** → **Add PostgreSQL**
2. Vänta ~2 minuter på att databasen skapas
3. Railway sätter automatiskt miljövariabeln `DATABASE_URL` på din app
   – den pekar på den nya Postgres-databasen. Klart!

## Steg 3 – Konfigurera e-post (Brevo / Gmail / SMTP)

För Brevo rekommenderas HTTPS API framför SMTP i Railway. HTTPS använder port
443 och undviker timeout-problem som kan uppstå när molnplattformar begränsar
SMTP-portar.

Lägg till följande variabler i Railway (under din app → **Variables**):

| Variabel | Värde |
|---|---|
| `BLOCKETVAKTEN_BREVO_API_KEY` | Din Brevo API-nyckel |
| `BLOCKETVAKTEN_EMAIL_FROM` | Verifierad avsändaradress i Brevo |
| `BLOCKETVAKTEN_HOST` | `0.0.0.0` |
| `BLOCKETVAKTEN_PORT` | `8080` |

Skapa API-nyckeln i Brevo under **SMTP & API → API Keys → Create a new API key**.
Börja med `xkeysib-` och visas bara en gång — spara den säkert. Använd inte
Brevos SMTP-lösenord som API-nyckel.

SMTP-variablerna kan lämnas kvar som fallback eller tas bort:
`BLOCKETVAKTEN_SMTP_HOST`, `BLOCKETVAKTEN_SMTP_PORT`, `BLOCKETVAKTEN_SMTP_USER`,
`BLOCKETVAKTEN_SMTP_PASSWORD` och `BLOCKETVAKTEN_SMTP_TLS`. Om
`BLOCKETVAKTEN_BREVO_API_KEY` finns använder appen API:et först.

Appen startar om automatiskt när du sparar variablerna.

## Steg 3b – Daglig backup (rekommenderas)

Appen kan automatiskt exportera hela databasen varje natt och skicka den
som en gzip-komprimerad JSON-fil till en webhook eller spara lokalt.

### Alternativ A: Webhook (t.ex. Discord, Slack, n8n, egen server)

Sätt `BLOCKETVAKTEN_BACKUP_URL` till en URL som tar emot POST-anrop:

| Variabel | Värde |
|---|---|
| `BLOCKETVAKTEN_BACKUP_URL` | `https://hooks.slack.com/services/...` |
| `BLOCKETVAKTEN_BACKUP_HOUR` | `3` (UTC, t.ex. 03:00 = mitt i natten svensk tid) |

Backupen skickas som `POST` med `Content-Encoding: gzip` och `Content-Type: application/json`.
Paketet är ett JSON-objekt med en nyckel per tabell ("users", "searches", "listings" osv.)
där varje nyckel innehåller en array av rader.

### Alternativ B: Lokal fil

Sätt URL:en till `file://`:

| Variabel | Värde |
|---|---|
| `BLOCKETVAKTEN_BACKUP_URL` | `file:///app/data/backup.json.gz` |

Backupen skrivs över varje natt (senaste versionen sparas).

### Testa backupen

```bash
# Kör en engångsbackup direkt (bra för att verifiera att URL:en fungerar)
set BLOCKETVAKTEN_BACKUP_URL=https://din-webhook-url
python backup.py
```

## Steg 4 – Migrera befintlig data (valfritt)

Om du redan har konton och bevakningar lokalt:

```powershell
# I PowerShell (Windows), från projektmappen:
$env:DATABASE_URL = "länken-till-din-railway-postgres"

# Installera psycopg2 om du inte redan har det:
pip install psycopg2-binary

# Kör migreringen:
python migrate_to_postgres.py
```

DATABASE_URL hittar du i Railway: klicka på PostgreSQL-databasen → **Connect** →
kopiera **Postgres Connection URL**.

## Steg 5 – Nya affärsfunktioner

Efter deploy skapar appen automatiskt följande additiva PostgreSQL-tabeller:

- `listing_finance` för kalkyl, faktiska köp-/försäljningskostnader och lagerstatus
- `listing_reminders` för uppföljningar
- `price_drop_settings` och `price_drop_events` för tröskelstyrda, deduplicerade prisfall

Du behöver inte köra ett separat SQL-script och befintliga tabeller raderas inte.
Startloggens `Databas OK` betyder att både grundschema och affärsschema kunde initieras.

I appen:

1. Öppna ett annonskort och expandera **Ekonomi & lager**.
2. Fyll i inköpspris, förväntad försäljning och kostnader.
3. Välj lagerstatus och spara. Efter försäljning fyller du i faktiskt pris och faktiska kostnader.
4. Använd **Lager** för bundet kapital och aktiva objekt.
5. Använd **Statistik** och välj 7, 30 eller 90 dagar för att jämföra bevakningar och kategorier.
6. Klicka **Följ pris** på en annons för att aktivera prisfallslarm. Standard är minst 500 kr eller 5 procent; **Prisfall** ändrar gränserna.

## Steg 6 – Testa

1. Railway ger dig en publik URL (t.ex. `https://blocketvakten.up.railway.app`)
2. Öppna länken i webbläsaren
3. Skapa ett konto eller logga in med ditt migrerade konto
4. Allt fungerar precis som tidigare!

## Uppdatera efter kodändringar

När funktionerna har testats lokalt:

```powershell
git add .
git commit -m "Lägg till affärskalkyl och lagerflöde"
git push
```

Railway deployar den nya versionen från GitHub. Databasmigrationerna körs vid
appstart och är avsedda att vara säkra att köra flera gånger.

## Arkitektur

- **Railway** hostar Python-appen + schemaläggaren (24/7)
- **PostgreSQL** (Railway-tillägg) ersätter SQLite – data försvinner aldrig
- **Schemaläggaren** körs i samma process som webbservern, precis som lokalt
- Appen använder `db/`-paketet som automatiskt väljer PostgreSQL när
  `DATABASE_URL` är satt, annars lokal SQLite

## Felsökning

| Problem | Lösning |
|---|---|
| Appen startar inte | Kolla Railway-loggarna under **Deployments** → **View Logs** |
| "DATABASE_URL is not set" | Se till att du lagt till PostgreSQL som en databas i Railway-projektet |
| "psycopg2 not found" | `requirements.txt` måste innehålla `psycopg2-binary` |
| E-post fungerar inte | Dubbelkolla miljövariablerna under **Variables**. Kom ihåg `BLOCKETVAKTEN_HOST=0.0.0.0` |
| `listing_finance` saknas | Kontrollera att senaste deployen körs och startloggen visar `Databas OK`. Starta om/redeploya appen så körs den additiva migrationen. |
| Statistik saknar faktisk vinst | Spara objektet som `Såld` och fyll i faktiskt försäljningspris samt faktiska kostnader i annonsens kalkyl. |
| Migrering misslyckas | Kontrollera att DATABASE_URL är korrekt och att din IP har åtkomst till Postgres |

## Kostnad

Railway har en generös gratistier: $5/mån kredit. En enkel Python-app + PostgreSQL
för personligt bruk ligger väl inom det. Du betalar först om du överskrider krediten
(t.ex. vid många samtidiga användare eller hög trafik).
