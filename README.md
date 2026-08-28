# Spelbarhetskoll – Tungelsta IF

Verktyg för tränarna i Tungelsta IFs A- och B-lag. Backend: FastAPI + SQLite. Frontend: React + Vite. Allt kör i en container.

---

## Miljövariabler

Kopiera `.env.example` till `.env` och fyll i `APP_PASSWORD`.

| Variabel | Beskrivning | Standardvärde |
|---|---|---|
| `APP_PASSWORD` | **Obligatorisk.** Lösenordet för hela appen. Minst 12 tecken rekommenderas. | – |
| `SEASON_ID` | iBIS säsongs-ID | `44` |
| `TEAM_A_ID` | iBIS team-ID för A-laget | `1977` |
| `TEAM_B_ID` | iBIS team-ID för B-laget | `17541` |
| `DATABASE_URL` | SQLAlchemy-URL till databasen | `sqlite:///./tungelsta.db` |

I Docker sätter `docker-compose.yml` `DATABASE_URL=sqlite:////data/tungelsta.db` automatiskt så att databasen hamnar i volymen.

---

## Bygga och köra med Docker

### Krav

- Docker Desktop (eller Docker Engine + Compose v2)

### Första gången

```sh
cp .env.example .env
# Sätt APP_PASSWORD i .env

docker compose up --build
```

Appen är tillgänglig på [http://localhost:8000](http://localhost:8000).

### Därefter

```sh
docker compose up
```

### Stoppa

```sh
docker compose down
```

Databasen ligger i Docker-volymen `tungelsta_data` och överlever omstarter och ombyggnader.

### Ta bort allt (inklusive databasen)

```sh
docker compose down -v
```

---

## Köra i utvecklingsläge (utan Docker)

Kräver Python 3.13+ och Node 20+.

**Backend:**

```sh
pip install -r requirements.txt
cp .env.example .env   # fyll i APP_PASSWORD
alembic upgrade head
uvicorn app.api:app --reload
```

**Frontend (separat terminal):**

```sh
cd frontend
npm install
npm run dev
```

Frontend-dev-servern proxar `/api` och `/auth` mot `http://localhost:8000`.

---

## Hälsokontroll

`GET /health` svarar `{"ok": true}` utan inloggning. Används av Docker och lastbalanserare.

---

## Datakälla och synk

Data hämtas från iBIS publika API. Synken körs automatiskt varje natt kl 03:00 (Europe/Stockholm) och kan även triggas manuellt via knappen i appen.

---

## Säkerhet

- Lösenordet lagras aldrig i klartext, aldrig i git
- Session-cookie är `HttpOnly`, signerad, giltig i 30 dagar
- iBIS-anrop sker från backend – aldrig från browsern
