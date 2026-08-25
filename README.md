# Blocketvakten

En webbapp som bevakar dina Blocket-sökningar och notifierar direkt när en ny
annons dyker upp – så slipper du scrolla manuellt varje dag.

Lokalt kan appen köras utan externa paket och lagrar data i SQLite under `data/`.
För PostgreSQL/molndrift installeras beroendena från `requirements.txt`.

## Funktioner

- **Sparade sökningar** – flera sökord/varianter per bevakning (OR-semantik),
  exkluderande ord, max-pris och valfritt område/ort. Pausa, redigera, ta bort.
- **Bevakning** – bakgrundsjobb hämtar Blocket-sökresultaten regelbundet,
  parsat annonserna och jämför annons-id mot tidigare sedda annonser.
  Varje annons notifieras bara en gång.
- **Notiser** – notis i webbläsaren (medan appen är öppen/fliken i bakgrunden)
  och samlade e-postnotiser per bevakning. Profilens e-postadress, SMTP och
  inställningen "Skicka e-post" per bevakning styr leveransen. Mailet innehåller
  titel, pris, bild (när den finns) och direktlänk.
- **Flöde/historik** – alla träffar per bevakning, nyast överst, med
  "sedd"/"intressant"-markering.
- **Prisinsikt & statistik** – snittpris, lägsta/högsta pris senaste 30 dagarna,
  totalantal och veckovis upptäcktshistorik. Annonser minst 15 % under snittet
  flaggas som "Bra pris". En översikt visar veckans total och bevakningen med
  flest nya annonser.
- **Prishistorik** – pris sparas vid varje kontroll. Graf per bevakning med
  lägsta-, högsta- och snittpris per dag.
- **Prisbevakning** – följ en specifik annons och få deduplicerat prisfallslarm. Ställ minsta prisfall i kronor och procent per annons.
- **Återförsäljningskalkyl** – spara inköpspris, förväntat försäljningspris, transport, reparation, avgifter, övriga kostnader och arbetstid. Appen visar totalkostnad, beräknad nettovinst, marginal, ROI och break-even. Efter försäljning kan verkligt pris och faktiska kostnader sparas separat.
- **Prioriteringslista** – Deal Score 0–100 väger samman rabatt, potentiell vinst, datakvalitet, annonsålder och riskord som "trasig" eller "reservdelar". Flödet kan sorteras efter bästa affär och filtreras efter lönsamhetsgräns.
- **Lagerflöde** – följ objekt från nytt fynd via kontakt, köp, renovering och publicering till såld, avstått eller förlorad. Spara kategori, kostnader, anteckningar och faktisk vinst.
- **Lönsamhetsstatistik** – välj 7, 30 eller 90 dagar och se investerat kapital, bundet kapital, faktisk/beräknad vinst, försäljningsgrad och resultat per bevakning och kategori.
- **Valbar kontrollfrekvens** – varje bevakning kan ställas in på
  1 minut / 30 min / 1 tim / 2 tim. Bakgrundsjobbet kollar bara bevakningar
  vars intervall förflutit.
- **SMS** – fält för telefonnummer i profilen och "Skicka SMS" per bevakning
  (UI + databas redo, SMS-sändning implementeras när provider valts).
- **Körhistorik** – tydlig fellogg om Blocket ändrar sin HTML och parsningen
  slutar hitta annonser (inget tyst misslyckande).

## Säkerhet

Appen stödjer nu **fleranvändarkonton** med:

- Registrering och inloggning (e-post + lösenord, PBKDF2-SHA256)
- Tokens i `Authorization: Bearer <token>`-header
- Sessioner med 30 dagars giltighetstid
- Varje användares data (bevakningar, annonser, notiser) är helt isolerad
- Lösenordslängd ≥ 4 tecken

Vid första start skapas en standardanvändare (user_id=0) så att befintliga
enkelanvändarinstallationer fortsätter fungera. Vid registrering av första
riktiga kontot migreras alla existerande bevakningar till det kontot.

## Moln-deploy (24/7 – datorn behöver inte vara igång)

Appen kan deployas till Railway/Render med PostgreSQL så att allt körs i molnet.
Konton, bevakningar, annonser, prishistorik och inställningar sparas permanent
och bakgrundsjobbet kollar Blocket dygnet runt utan att din dator är på.

👉 **Komplett guide:** [DEPLOY.md](DEPLOY.md)

Snabbversionen:

1. Pusha repot till GitHub
2. Skapa ett Railway-konto → **Deploy from GitHub repo**
3. Lägg till PostgreSQL och koppla dess `DATABASE_URL` till appen
4. Sätt Brevo API-variabler under **Variables**
5. Kör `python migrate_to_postgres.py` för att flytta befintlig data

Appen byter automatiskt till PostgreSQL när `DATABASE_URL` är satt – lokalt
fortsätter SQLite fungera precis som tidigare. Vid start skapas nya affärstabeller
med additiva migrationer; befintliga konton, bevakningar och annonser raderas inte.

## Affärsfunktioner

Öppna en annons i flödet och expandera **Ekonomi & lager** för att fylla i kalkylen
och välja lagerstatus. Knappen **Lager** visar sparade objekt och bundet kapital.
Under **Statistik** kan du välja period och jämföra vilka bevakningar och kategorier
som ger faktisk vinst. **Följ pris** aktiverar ett prisfallslarm med standardgränsen
500 kr eller 5 procent; inställningen kan ändras med **Prisfall**.

Alla affärstabeller innehåller `user_id` och API:t kontrollerar dessutom att den
kopplade bevakningen tillhör den inloggade användaren.

## Köra

```bash
python app.py            # startar webbappen på http://127.0.0.1:8080
python app.py --check    # kör en kontroll av alla bevakningar och avslutar
python app.py --init     # skapar bara databasen
```

Öppna sedan http://127.0.0.1:8080 i webbläsaren.

Den inbyggda schemaläggaren kollar aktiva bevakningar var 60:e sekund. För
serverdrift kan du stänga av den och låta cron köra `python app.py --check`
var 15:e minut:

```bash
BLOCKETVAKTEN_DISABLE_SCHEDULER=1 python app.py
*/15 * * * * cd /sökväg/till/app && python app.py --check
```

## Konfiguration (miljövariabler)

| Variabel | Standard | Beskrivning |
| --- | --- | --- |
| `BLOCKETVAKTEN_PORT` | `8080` | Port för webbappen |
| `BLOCKETVAKTEN_HOST` | `127.0.0.1` | Bind-adress |
| `BLOCKETVAKTEN_SCHEDULER_TICK` | `60` | Hur ofta (sek) schemaläggaren vaknar och ser efter om någon bevakning är redo |
| `BLOCKETVAKTEN_CHECK_INTERVAL` | `60` | (Föråldrad – används ej. Varje bevakning har nu eget intervall.) |
| `BLOCKETVAKTEN_DISABLE_SCHEDULER` | tom | `1` stänger av den inbyggda schemaläggaren |
| `BLOCKETVAKTEN_GOOD_PRICE_RATIO` | `0.85` | Tröskel för "bra pris" (andel av snittpriset) |
| `BLOCKETVAKTEN_SMTP_HOST` | tom | SMTP-server för e-post (valfritt) |
| `BLOCKETVAKTEN_SMTP_PORT` | `587` | SMTP-port |
| `BLOCKETVAKTEN_SMTP_USER` | tom | SMTP-användare |
| `BLOCKETVAKTEN_SMTP_PASSWORD` | tom | SMTP-lösenord |
| `BLOCKETVAKTEN_SMTP_TLS` | `1` | Använd STARTTLS |
| `BLOCKETVAKTEN_EMAIL_FROM` | tom | Avsändaradress |
| `BLOCKETVAKTEN_EMAIL_TO` | tom | Legacy-fallback för profilens mottagaradress |
| `BLOCKETVAKTEN_BREVO_API_KEY` | tom | Brevo API-nyckel (rekommenderas i Railway; använder HTTPS/443) |
| `BLOCKETVAKTEN_BREVO_API_URL` | Brevos standard-URL | Valfri egen Brevo API-URL |

E-postadressen anges sedan under **Profil & inställningar**. SMTP-variablerna
konfigurerar bara transporten; varje bevakning har dessutom en egen
**Skicka e-post**-toggle så att du kan välja vilka bevakningar som får e-post.
## Gmail SMTP – steg för steg

Gmail kräver ett **applösenord** (inte ditt vanliga lösenord) för att tillåta
SMTP-åtkomst från tredjepartsappar. Här är hela guiden från början till ett
fungerande flöde.

### 1. Aktivera tvåfaktorsautentisering (2FA)

Om du inte redan har tvåfaktorsautentisering på ditt Google-konto:

1. Gå till [myaccount.google.com](https://myaccount.google.com/)
2. Klicka på **Säkerhet** i vänstermenyn
3. Under "Så loggar du in på Google", klicka på **Tvåstegsverifiering**
4. Följ instruktionerna – du behöver en telefon för verifieringskoden

### 2. Skapa ett applösenord

1. Gå till [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   (eller: Säkerhet → Tvåstegsverifiering → Applösenord, längst ner på sidan)
2. Under "Välj app" väljer du **E-post**
3. Under "Välj enhet" väljer du **Annat (anpassat namn)** och skriver "Blocketvakten"
4. Klicka **Generera**
5. Google visar ett 16-teckens lösenord i fyra grupper om fyra tecken
   (t.ex. `abcd efgh ijkl mnop`). **Kopiera det direkt** – du ser det bara en
   gång. Klistra in det i en textfil temporärt om du inte ska använda det direkt.

### 3. Sätt miljövariablerna och starta appen

**Windows (PowerShell):**

```powershell
$env:BLOCKETVAKTEN_SMTP_HOST = "smtp.gmail.com"
$env:BLOCKETVAKTEN_SMTP_PORT = "587"
$env:BLOCKETVAKTEN_SMTP_USER = "din.email@gmail.com"
$env:BLOCKETVAKTEN_SMTP_PASSWORD = "det-16-teckens-applosenordet"
$env:BLOCKETVAKTEN_SMTP_TLS = "1"
$env:BLOCKETVAKTEN_EMAIL_FROM = "din.email@gmail.com"
python app.py
```

**Linux / Mac (bash):**

```bash
BLOCKETVAKTEN_SMTP_HOST=smtp.gmail.com \
BLOCKETVAKTEN_SMTP_PORT=587 \
BLOCKETVAKTEN_SMTP_USER=din.email@gmail.com \
BLOCKETVAKTEN_SMTP_PASSWORD="det-16-teckens-applosenordet" \
BLOCKETVAKTEN_SMTP_TLS=1 \
BLOCKETVAKTEN_EMAIL_FROM=din.email@gmail.com \
python app.py
```

### 4. Ange din e-postadress i appens gränssnitt

1. Öppna http://127.0.0.1:8080 i webbläsaren
2. Logga in på ditt konto
3. Gå till **Profil & inställningar**
4. Fyll i din e-postadress i fältet **E-postadress för notiser**
5. Klicka **Spara profil**
6. För varje bevakning du vill ha e-post för: öppna bevakningen, klicka
   **Redigera bevakning**, och slå på **Skicka e-post för nya träffar**

### Felsökning

| Felmeddelande i terminalen | Trolig orsak |
|---|---|
| `(535, b'5.7.8 Username and Password not accepted')` | Du använder ditt vanliga Google-lösenord istället för applösenordet. Skapa ett nytt applösenord (steg 2 ovan). |
| `(534, b'5.7.9 Application-specific password required')` | Tvåfaktorsautentisering är inte aktiverat på kontot, eller så har du inte skapat något applösenord än. Gå igenom steg 1 och 2. |
| `[notifier] E-post misslyckades` (utan SMTP-felkod) | Kontrollera att `BLOCKETVAKTEN_EMAIL_FROM` matchar `BLOCKETVAKTEN_SMTP_USER` (samma Gmail-adress). Kontrollera också att lösenordet inte har mellanslag före/efter. |
| `[notifier] E-post misslyckades: [SSL: WRONG_VERSION_NUMBER]` | `BLOCKETVAKTEN_SMTP_PORT` är fel. Gmail använder port **587** med TLS. |

### Andra e-postleverantörer

Samma miljövariabler fungerar för andra leverantörer. Exempel:

| Leverantör | SMTP_HOST | SMTP_PORT |
|---|---|---|
| Outlook / Hotmail | `smtp.office365.com` | `587` |
| Yahoo | `smtp.mail.yahoo.com` | `587` |
| SendGrid | `smtp.sendgrid.net` | `587` |
| Mailgun | `smtp.mailgun.org` | `587` |

> **Notis om lösenordsåterställning:** Återställningslänkar ("Glömt lösenord?")
> skickas också via SMTP. Om SMTP inte är konfigurerat visas
> återställningslänken direkt i appens gränssnitt istället, så du kan alltid
> återställa ett glömt lösenord.

SMS konfigureras via miljövariabler för framtida bruk:

| Variabel | Standard | Beskrivning |
| --- | --- | --- |
| `BLOCKETVAKTEN_SMS_ENABLED` | tom | `1` aktiverar SMS-sändning |
| `BLOCKETVAKTEN_SMS_API_URL` | tom | API-url för SMS-tjänst |
| `BLOCKETVAKTEN_SMS_API_KEY` | tom | API-nyckel |
| `BLOCKETVAKTEN_SMS_FROM` | `Blocketvakten` | Avsändarnamn/nummer |

Exempel med e-post (snabbstart):

```bash
BLOCKETVAKTEN_SMTP_HOST=smtp.gmail.com \
BLOCKETVAKTEN_SMTP_USER=du@gmail.com \
BLOCKETVAKTEN_SMTP_PASSWORD=applosenord \
BLOCKETVAKTEN_EMAIL_FROM=du@gmail.com \
python app.py
```

## Hur bevakningen fungerar

1. Varje sökord i en bevakning byggs till en Blocket-sök-URL:
   `https://www.blocket.se/recommerce/forsale/search?q=<sökord>&price_to=<maxpris>`.
2. Bakgrundsjobbet hämtar sidans HTML och parsar annonskorten
   (`<article class="sf-search-ad">`) – titel, pris, bild, plats, annons-id och
   en relativ publiceringstid ("25 min", "1 dag" ...).
3. Annons-id:t sparas i SQLite. Är det nytt för bevakningen skapas en notis;
   finns det redan hoppas det över. Annons-id:t utläses ur annonslänken
   (`/item/<id>`).
4. Om sidan inte längre innehåller några annonskort loggas ett tydligt fel i
   körhistoriken (`ParseError`) istället för att tyst göra ingenting.

### Begränsningar / viktigt att veta

- Blocket har ingen publik API för tredjepartsappar, så vi tolkar den
  serverrenderade sökresultatsidan. Ändrar Blocket markupen måste parsern
  uppdateras (körhistoriken gör det lätt att upptäcka).
- Sökningen hämtar första sidan (≈53 nyaste annonserna) per sökord. Det räcker
  för en bevakning som körs ofta, men mycket gamla annonser utanför första
  sidan plockas inte upp retroaktivt.
- **Plats/ort** filtreras app-sidigt mot platsen på annonskortet (t.ex.
  "Stockholm") eftersom Blocket inte exponerar en stabil plats-parameter för
  tredjepartsbruk. För bäst träffbild, använd en ort som Blocket visar på
  korten.
- "Publicerad-datum" uppskattas från Blockets relativa tidsangivelse; exakta
  klockslag exponeras inte i sidan.

## Tester

```bash
python -m unittest discover -s tests -v
```

## Struktur

- `app.py` – HTTP-server, JSON-API och schemaläggare
- `blocket.py` – bygger sök-URL:er, hämtar och parsar Blocket-HTML
- `monitor.py` – filtrering, dedupe på annons-id och notis-skapande
- `db/` – SQLite- och PostgreSQL-datalager med additiva migrationer
- `business.py` – användarägd ekonomi, lager, påminnelser, prisfall och statistik
- `profit.py` – rena formler för nettovinst, marginal, ROI, risk och Deal Score
- `notifier.py` – e-post via Brevo HTTPS API eller SMTP
- `config.py` – inställningar
- `static/` – mobil-först frontend (vanilla JS)
