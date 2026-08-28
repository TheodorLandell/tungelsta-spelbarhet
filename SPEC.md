# Spelbarhetskoll – Tungelsta IF

Verktyg för tränarna i Tungelsta IF:s A- och B-lag. Tre delar:

1. **Spelbarhet** – vilka spelare som får användas i B-laget och hur nära de är
   att bli låsta i A-laget
2. **Matchvy** – kommande och spelade matcher, med manuell skottregistrering
   under matchens gång
3. **Statistik** – mål, assist, utvisningsminuter och skottstatistik per spelare
   och lag

Alla tre delar används av tränarna i båda lagen. Lagväljaren är ett filter, inte
en behörighetsspärr.

---

## 1. Reglerna

Två separata regler. Båda leder till samma påföljd: spelaren blir **låst i A-laget**
och får inte längre spela med B-laget.

**Kvalificeringsregeln.** En spelare måste stå uppskriven i en B-lagsmatch innan sin
första A-lagsmatch. Gör han sin första match i A-laget blir han låst direkt.

**Kedjeregeln.** Max två A-lagsmatcher i rad. Den tredje i rad låser spelaren.
Kedjan nollställs endast av att spelaren står över en A-match, alltså inte finns med
i matchtruppen. Att spela en B-match emellan bryter *inte* kedjan.

Detaljer som spelar roll:

- "Spelat en match" betyder **uppskriven i matchtruppen**, inte tid på plan.
- Endast seriematcher räknas. Cup och träningsmatcher ignoreras.
- Kedjan räknas i **speldatumordning**, inte omgångsnummer. En uppskjuten match
  hamnar där den faktiskt spelades.
- Inställda matcher hoppas över helt. De varken ökar eller nollställer kedjan.
- När en spelare är låst slutar all räkning. Han visas bara som låst.
- Reglerna gäller likadant hela säsongen. Inga undantag för målvakter.
- C-laget (Herrar Division 5 Sydvästra) omfattas inte och ska ignoreras.

Reglerna är implementerade och testade i `eligibility.py`. **Skriv inte om
den logiken** – bygg runt den. Testerna i `test_eligibility.py` måste fortsätta
gå igenom.

---

## 2. Lagen

| Roll | Lag | TeamID | Serie |
|------|-----|--------|-------|
| A | Tungelsta IF (A) | `1977` | Herrar Division 2 |
| B | Tungelsta IF (B) | `17541` | Herrar Division 5 Sydöstra |

Säsong 2026/27 har `SeasonID = 44`. Ligger i konfiguration, inte hårdkodat.

---

## 3. Datakälla: iBIS publika API

Bas-URL: `https://api.innebandy.se/v2/api`

Ingen autentisering krävs. **Men** svaren sätter
`Access-Control-Allow-Origin: https://stats.innebandy.se`, så anropen måste göras
från backend. Frontend får aldrig anropa iBIS direkt.

### 3.1 Lagets matcher

```
GET /seasons/{seasonId}/teams/{teamId}
```

Returnerar ett lagobjekt med `Competitions[]`. Varje tävling har:

| Fält | Användning |
|------|------------|
| `CompetitionID` | Identifierar tävlingen |
| `CompetitionTypeID` | **`1` = serie, `3` = cup.** Filtrera på `== 1` |
| `Name` | Visningsnamn, t.ex. `"Herrar Division 2"` |
| `Matches[]` | Alla matcher i tävlingen |

Lagobjektet innehåller även `Players[]` (hela truppen) och `TeamPersons[]` (ledare).

### 3.2 Matchobjektet

| Fält | Typ | Användning |
|------|-----|------------|
| `MatchID` | int | Nyckel, används mot lineups |
| `MatchDateTime` | `"2026-09-19T13:00:00"` | Sorteringsnyckel. Naiv lokaltid, Europe/Stockholm |
| `Cancelled` | bool | Inställd → hoppa över helt |
| `Postponed` | bool | Uppskjuten. `MatchDateTime` speglar nya datumet |
| `Abandoned` | bool | Avbruten match, se 3.4 |
| `MatchStatus` | int | Odokumenterad enum, använd inte som enda källa |
| `GoalsHomeTeam` / `GoalsAwayTeam` | int/null | `null` = ej spelad |
| `FinalResultCreatedTS` | str/null | `null` = ej färdigrapporterad |
| `HomeTeamID` / `AwayTeamID` | int | Avgör om Tungelsta är hemma eller borta |
| `HomeTeam` / `AwayTeam` | str | Motståndarens namn |
| `Round` / `RoundName` | int/str | Endast visning, aldrig sortering |
| `Venue` / `MainVenue` | str | Visning i matchvyn |

### 3.3 Matchtrupp och spelarstatistik

```
GET /matches/{matchId}/lineups
```

Returnerar `HomeTeamPlayers[]` och `AwayTeamPlayers[]`. Välj rätt array genom att
jämföra `HomeTeamID` / `AwayTeamID` mot vårt `teamId`.

| Fält | Användning |
|------|------------|
| `PlayerID` | **Primär identitet.** Stabil mellan matcher och serier |
| `MatchPlayerID` | Unikt per match. Använd aldrig som spelaridentitet |
| `Name` | Visning |
| `ShirtNo` | Visning. Kan vara null |
| `Goals` | Mål i matchen. Källa för del 3 |
| `Assists` | Assist i matchen. Källa för del 3 |
| `PenaltyMinutes` | Utvisningsminuter. Källa för del 3 |
| `Points` | Mål + assist. Härledd, använd inte |
| `PositionID` / `Position` | Målvaktsmarkering om ifylld |
| `LicensedAssociationID` | Sanity-check att spelaren tillhör Tungelsta |

Truppen läggs upp i iBIS före matchstart, så lineups kan finnas även för matcher
som ännu inte spelats. Endast spelade matcher räknas i regelmotorn (se 3.4).

### 3.4 Vad räknas som spelad

En match räknas i regelberäkningen om **alla** stämmer:

- `Cancelled == false`
- `CompetitionTypeID == 1`
- `FinalResultCreatedTS != null` **eller** (`MatchDateTime` har passerat och
  `GoalsHomeTeam != null`)

`Abandoned == true` med registrerat resultat räknas som spelad. Avbruten match utan
resultat hoppas över och loggas som varning i synken.

### 3.5 Synk

Ett jobb en gång per dygn, plus en manuell "Uppdatera"-knapp.

1. Hämta båda lagens matchlistor
2. Filtrera på `CompetitionTypeID == 1`
3. Spara/uppdatera trupperna från `Players[]` för båda lagen (upsert på
   `PlayerID`, skriv aldrig över befintligt tröjnummer med null)
4. För varje spelad match som saknas: hämta lineups, spara appearances
   inklusive `Goals`, `Assists`, `PenaltyMinutes`
5. Logga till `sync_log`
6. Räkna om regelmotorn och cacha resultatet

Sekventiella anrop med kort paus. Färdigrapporterade matcher hämtas inte om.

---

## 4. Datamodell

```
matches
  match_id          PK
  team              'A' | 'B'
  competition_id
  kickoff           datetime
  status            'played' | 'scheduled' | 'cancelled' | 'abandoned'
  round_name
  opponent
  venue
  raw               json

appearances
  match_id          FK
  player_id
  player_name
  shirt_no
  goals             int
  assists           int
  penalty_minutes   int
  source            'ibis' | 'manual'
  PRIMARY KEY (match_id, player_id)

players
  player_id         PK
  name
  shirt_no
  is_goalkeeper     bool
  last_seen

shot_events
  id                PK, UUID skapad på klienten
  match_id          FK
  player_id         FK
  kind              'on_goal' | 'missed' | 'blocked'
  period            1 | 2 | 3
  created_at        datetime
  created_by        text
  deleted_at        datetime, null om aktiv

overrides
  id                PK
  player_id         FK
  kind              'lock' | 'unlock' | 'set_matches_left'
  value             int, null för lock/unlock
  note              text
  created_at        datetime
  created_by        text
  data_snapshot     datetime

roster_edits
  id                PK
  match_id          FK
  player_id         FK
  action            'add' | 'remove'
  note              text
  created_at        datetime
  created_by        text

sync_log
  id, started_at, finished_at, matches_added, warnings json, ok bool
```

SQLite räcker. En säsong är cirka 40 matcher och 54 spelare.

---

## 5. Del 1 – Spelbarhet

Redan byggd. Två tillägg:

- Redigeringsläget ska kunna **både låsa och låsa upp** en spelare, inte bara
  låsa upp
- Lagväljaren högst upp gäller hela appen och styr vilken match- och
  spelarlista som visas

I övrigt oförändrad: grupperad lista (måste stå över / tillgängliga / låsta),
skrivskyddad tills redigera-knappen aktiveras, overrides som appliceras efter
beräkningen och aldrig rör rådatan.

---

## 6. Del 2 – Matchvy och skottregistrering

### 6.1 Matchlistan

Hela säsongens matchlista för valt lag, i datumordning. Vyn ska automatiskt
scrolla till nästa kommande match så tränaren slipper leta. Spelade matcher går
alltid att öppna och ändra i.

Per match: datum, tid, motståndare, hemma/borta, hall, resultat om spelat.

### 6.2 Registreringsvyn

Öppnas genom att klicka på en match. Visar matchens trupp och låter tränaren
registrera skott per spelare.

**Tre kategorier registreras manuellt:**

- `on_goal` – skott på mål
- `missed` – skott utanför
- `blocked` – skott i täck

**Mål registreras inte manuellt.** De hämtas från iBIS och är en egen kategori.
Tränaren ska alltså *inte* trycka när ett skott går in.

**Totala skott** = mål + skott på mål + skott utanför + skott i täck. Räknas fram,
knappas aldrig in.

**Kontroller.** Per spelare och kategori: en stor plusknapp som visar antalet, och
en smalare minusknapp under. Plus är den vanliga handlingen och ska gå att träffa
med tummen utan att titta. Minus finns tillgängligt men tar mindre plats.

**Periodväljare** överst med P1, P2, P3. Den **taggar** registreringen: det som
trycks medan P2 är valt sparas som period 2. Vald period ska synas tydligt, och
appen bör påminna vid periodbyte eftersom det annars är lätt att glömma.

**Målvakter** visas i listan som alla andra, markerade med MV.

**Målrutan är tom under matchen** eftersom målen kommer från iBIS med fördröjning.
Det ska framgå tydligt att totalen och procenten är ofullständiga tills synk skett,
inte se ut som att spelarna saknar mål.

**Avstämning efter synk.** När iBIS-målen kommit in visar matchvyn en rad i stil
med "iBIS: 4 mål. Registrerat: 18 skott på mål." Så syns det om någon råkat
registrera mål som skott på mål.

### 6.3 Local-first

Registreringen måste fungera utan nät. Sporthallar har ofta dålig täckning, och
mitt i en match finns ingen tid att felsöka.

- Varje tryck sparas omedelbart lokalt (IndexedDB) och visas direkt i UI
- Varje händelse får ett UUID som skapas på klienten, så omsynk aldrig ger
  dubbletter
- Synk mot servern sker i bakgrunden när nät finns
- Borttagning är en tombstone (`deleted_at`), inte en radering
- Matchens trupp cachas när matchen öppnas, så den finns kvar offline
- UI visar tydligt om något ligger osynkat lokalt

### 6.4 Flera tränare samtidigt

Registreringar laddas upp direkt och andra enheter hämtar dem, så tränarna ser
varandras inmatning.

En liten flödesrad visar de senaste registreringarna med spelare, kategori och
vem som tryckte. Den gör det lätt att se vad den andra gjort och att ångra fel.

Tekniska dubbletter är omöjliga tack vare klient-UUID. Att två personer
registrerar samma skott är ett mänskligt problem som flödesraden ska göra synligt.

### 6.5 Ändra matchlista

En knapp längst ner i registreringsvyn öppnar redigering av matchens trupp: lägga
till eller ta bort spelare.

Ändringen sparas i `roster_edits` och **påverkar både skottvyn och
regelberäkningen**, eftersom syftet är att rätta fel i underlaget.

Två krav som följer av det:

- Varje ändring loggas med tidpunkt och anteckning, och går att ångra
- När en tillagd eller borttagen spelare påverkar låsstatus ska det synas i
  del 1 med en markering om att underlaget är manuellt ändrat, på samma sätt
  som overrides

Regelmotorn får sina appearances från iBIS plus roster_edits. Rådatan från iBIS
skrivs aldrig över – ändringarna ligger som ett separat lager ovanpå.

---

## 7. Del 3 – Statistik

Per spelare, för valt lag och vald omfattning.

| Värde | Källa |
|-------|-------|
| Matcher | Antal matcher i truppen under säsongen |
| Mål | iBIS |
| Assist | iBIS |
| Poäng | Mål + assist |
| Utvisningsminuter | iBIS |
| Skott totalt | Mål + på mål + utanför + i täck |
| Mål | antal och andel av totala skott |
| På mål | antal och andel av totala skott |
| Utanför | antal och andel av totala skott |
| I täck | antal och andel av totala skott |

De fyra andelarna summerar till 100 %.

**Urval.** Överst väljer man lag och omfattning: senaste matchen, de senaste N
matcherna, eller hela säsongen. N ska gå att sätta enkelt, utan att skriva in
siffror i ett fält.

**Lagseparation.** En spelare som spelat i båda lagen får statistiken uppdelad per
lag. Varje match hör redan till lag A eller B, så en spelares siffror hör till
matchens lag. A-tränaren ser hans A-siffror, B-tränaren hans B-siffror.

**Saknad data.** Skott finns bara för matcher där någon registrerat. Visa tomt
eller en markering, inte noll – noll skott och ingen registrering är olika saker.

---

## 8. Åtkomst

Ett gemensamt lösenord framför hela appen. Signerad session-cookie som håller i
30 dagar. Lösenordet läses från miljövariabel, jämförs hashat, checkas aldrig in.

Inga användarkonton och ingen rollhantering. Lagväljaren är ett filter.

Registreringar sparar `created_by`. Eftersom det inte finns konton får tränaren
ange ett kortnamn en gång, som sparas lokalt och följer med registreringarna.

---

## 9. Stack

- Backend: Python, FastAPI, SQLite via SQLAlchemy
- Frontend: React + Vite, Tailwind
- Lokal lagring: IndexedDB
- Synk: schemalagt jobb en gång per dygn plus manuell trigger
- Deploy: en container, egen domän

Mobilen är den primära enheten genomgående. Bygg mobile-first.

---

## 10. Utanför scope

- Motståndarnas skott
- Förvarning baserad på publicerade men ospelade trupper
- C-laget, dam- och juniorlag
- Cup- och träningsmatcher
- Val av säsong i gränssnittet
- Användarkonton, roller, notifieringar
- Målvaktsstatistik utöver mål, assist och utvisningar

---

## 11. Felmeddelanden

Felmeddelanden i UI måste spegla vad som faktiskt gick fel. Visa aldrig ett
specifikt fel ("fel lösenord", "synken misslyckades", "aldrig synkad") när
orsaken kan vara ett nätverksfel eller ett annat statuskodsvar. Skilj alltid på
att servern inte svarar och att servern svarat med ett fel.

---

## 12. Att bygga i ordning

Steg 1–8 är klara (konfig, iBIS-klient, synk, regelmotor, backend-API, listvy,
lösenordsgrind, redigeringsläge).

9. Deploy av del 1
10. Lagväljare i hela appen, plus lås/lås upp i redigeringsläget
11. Utöka synken med Goals, Assists, PenaltyMinutes och målvaktsmarkering
12. Matchlistan och matchvyn, skrivskyddad
13. Skottregistrering med local-first lagring
14. Synk av skott mot servern, flödesrad för flera tränare
15. Ändra matchlista, roster_edits och koppling mot regelmotorn
16. Avstämning mot iBIS-mål i matchvyn
17. Statistiksidan

Steg 13 är det mest kritiska. Testa det i en verklig hall, med flygplansläge på,
innan det används skarpt. Ett skott som inte registreras går inte att få tillbaka.