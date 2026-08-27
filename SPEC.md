# Spelbarhetskoll – Tungelsta IF

Verktyg för tränarna i Tungelsta IF som visar vilka spelare som fortfarande får
användas i B-laget, och hur nära de är att bli låsta i A-laget.

Reglerna kommer från Stockholms Innebandyförbund. Idag räknas de för hand, vilket
är lätt att göra fel på. Ett fel kostar poäng på grönt bord.

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

Reglerna är redan implementerade och testade i `eligibility.py`. **Skriv inte om
den logiken** – bygg runt den. `test_eligibility.py` innehåller 18 tester som måste
fortsätta gå igenom.

---

## 2. Lagen

| Roll | Lag | TeamID | Serie |
|------|-----|--------|-------|
| A | Tungelsta IF (A) | `1977` | Herrar Division 2 |
| B | Tungelsta IF (B) | `17541` | Herrar Division 5 Sydöstra |

Säsong 2026/27 har `SeasonID = 44`.

Dessa tre värden ligger i konfiguration, inte hårdkodade i logiken. Säsongsbyte
hanteras genom att ändra konfigurationen – ingen UI för det behövs nu.

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

Relevanta fält:

| Fält | Typ | Användning |
|------|-----|------------|
| `MatchID` | int | Nyckel, används mot lineups |
| `MatchDateTime` | `"2026-09-19T13:00:00"` | Sorteringsnyckel. Naiv lokaltid, Europe/Stockholm |
| `Cancelled` | bool | Inställd → hoppa över helt |
| `Postponed` | bool | Uppskjuten. `MatchDateTime` speglar nya datumet, så matchen räknas normalt |
| `Abandoned` | bool | Avbruten match, se 3.4 |
| `MatchStatus` | int | Odokumenterad enum, använd inte som enda källa |
| `GoalsHomeTeam` / `GoalsAwayTeam` | int/null | `null` = ej spelad |
| `FinalResultCreatedTS` | str/null | `null` = ej färdigrapporterad |
| `HomeTeamID` / `AwayTeamID` | int | Avgör om Tungelsta är hemma eller borta |
| `Round` / `RoundName` | int/str | Endast visning, aldrig sortering |

### 3.3 Matchtrupp

```
GET /matches/{matchId}/lineups
```

Returnerar `HomeTeamPlayers[]` och `AwayTeamPlayers[]`. Välj rätt array genom att
jämföra `HomeTeamID` / `AwayTeamID` mot vårt `teamId`.

Spelarobjektet:

| Fält | Användning |
|------|------------|
| `PlayerID` | **Primär identitet.** Stabil mellan matcher och serier |
| `MatchPlayerID` | Unikt per match. Använd aldrig som spelaridentitet |
| `Name` | Endast visning |
| `ShirtNo` | Visning |
| `LicensedAssociationID` | Sanity-check att spelaren tillhör Tungelsta |

Truppen läggs upp i iBIS före matchstart, så lineups kan finnas även för matcher
som ännu inte spelats. Endast spelade matcher ska räknas (se 3.4).

### 3.4 Vad räknas som spelad

En match räknas i beräkningen om **alla** stämmer:

- `Cancelled == false`
- `CompetitionTypeID == 1`
- `FinalResultCreatedTS != null` **eller** (`MatchDateTime` har passerat och
  `GoalsHomeTeam != null`)

`Abandoned == true` med registrerat resultat räknas som spelad. Avbruten match utan
resultat hoppas över och loggas som en varning i synken – det är ett gränsfall som
inte är bekräftat mot förbundet, så det ska synas snarare än tyst antas.

### 3.5 Synk

Ett jobb som körs en gång per dygn, plus en manuell "Uppdatera"-knapp i UI:t.

1. Hämta båda lagens matchlistor
2. Filtrera på `CompetitionTypeID == 1`
3. För varje match som räknas som spelad och saknas i databasen: hämta lineups
4. Spara matcher och uppskrivningar
5. Kör om regelmotorn och cacha resultatet

Lineups för en match som redan är sparad och färdigrapporterad hämtas inte om.
Respektera källan: sekventiella anrop, kort paus mellan, ingen parallell hammer.
En full första synk är cirka 45 anrop.

---

## 4. Datamodell

```
matches
  match_id          PK
  team              'A' | 'B'
  competition_id
  kickoff           datetime
  status            'played' | 'scheduled' | 'cancelled'
  round_name
  opponent
  raw               json

appearances
  match_id          FK
  player_id
  player_name
  shirt_no
  PRIMARY KEY (match_id, player_id)

players
  player_id         PK
  name
  shirt_no
  last_seen

overrides
  id                PK
  player_id         FK
  kind              'unlock' | 'set_matches_left'
  value             int, null för unlock
  note              text
  created_at        datetime
  created_by        text
  data_snapshot     datetime   -- senaste matchdatum när ändringen gjordes

sync_log
  id, started_at, finished_at, matches_added, warnings json, ok bool
```

SQLite räcker gott. Appen har en handfull användare och en säsong innehåller
ungefär 44 matcher.

---

## 5. Regelmotorn

`eligibility.py` medföljer färdig. Den exporterar:

```python
compute_statuses(matches, appearances, *, include_scheduled=False, strict_ties=True)
    -> (dict[player_id, PlayerStatus], list[str])

blocked_for_b(statuses)   -> list[PlayerStatus]
available_for_b(statuses) -> list[PlayerStatus]
```

`PlayerStatus` innehåller `locked`, `lock_reason`, `lock_date`, `lock_match_id`,
`consecutive_a`, `matches_left`, `warning`, `a_match_ids`, `b_match_ids`.

Backendens uppgift är att läsa ur databasen, mappa till `Match` och `Appearance`,
anropa `compute_statuses` och servera resultatet. Ingen regellogik utanför modulen.

Varningarna i returvärdet (t.ex. A- och B-match med identisk starttid) ska visas i
UI:t, inte sväljas.

---

## 6. Manuella ändringar

Beräkningen kan bli fel om iBIS-datan är fel eller ofullständig. Tränarna behöver
kunna korrigera, men det får inte gå att råka göra.

**Interaktion.** En redigera-knapp i vyns hörn. I normalläge är listan helt
skrivskyddad. Vid klick går vyn in i redigeringsläge, kontrollerna blir aktiva och
knappen byter till "Klar". Ingen inline-redigering utan att läget är på.

**Vad som går att ändra per spelare.**

- Låsa upp en låst spelare
- Sätta antal matcher kvar manuellt (0–2)

**Semantik.** Overrides lagras separat och appliceras *efter* beräkningen. De skriver
aldrig över rådatan. En spelare med aktiv override visar en diskret markering plus
anteckning och datum, och har en "Återställ"-knapp som tar bort overriden.

Om ny matchdata tillkommit efter att overriden skapades (jämför mot
`data_snapshot`), visa en varning vid spelaren: ändringen kan vara inaktuell.
Ta inte bort den automatiskt – tränaren bestämmer.

---

## 7. Gränssnitt

En enda vy. Mobilanpassad och responsiv – tränarna använder den i hallen, på
telefon, inte vid en dator.

### Överst

- Lagnamn och tidpunkt för senaste lyckade synk
- Uppdatera-knapp
- Tre räknare: tillgängliga, måste stå över, låsta

### Listan

Grupperad, i denna ordning:

**1. Måste stå över nästa A-match** – spelare med `matches_left == 0`. Inte låsta än,
men en A-match från att bli det. Enda gruppen som kräver handling, därför överst och
med varningsfärg.

**2. Tillgängliga** – sorterade med lägst `matches_left` först.

**3. Låsta i A-laget** – dämpad stil, med låsorsak utskriven i klartext
("Tre A-matcher i rad" / "Spelade A före B") och datumet det hände, så det går att
kontrollera mot iBIS.

Varje rad: tröjnummer, namn, kort rad med underlag (vilka A-matcher som ligger i
kedjan), och en statusbadge till höger.

Spelare i truppen som ännu inte spelat någon match visas som tillgängliga med
markeringen att de måste spela B först.

### Ton

Sentence case, svenska genomgående. Inga utropstecken. Statusbadgen ska gå att läsa
på en meter avstånd.

---

## 8. Åtkomst

Ett gemensamt lösenord framför hela appen. Formulär vid första besöket, sedan en
signerad session-cookie som håller i minst 30 dagar så tränarna slipper skriva in
det varje gång.

Lösenordet läses från miljövariabel, aldrig hårdkodat och aldrig incheckat i git.
Jämför hashat, inte i klartext.

Inga användarkonton, ingen rollhantering.

---

## 9. Stack

- Backend: Python, FastAPI, SQLite via SQLAlchemy
- Frontend: React + Vite, Tailwind
- Synk: schemalagt jobb en gång per dygn plus manuell trigger
- Deploy: en container, egen domän

---

## 10. Utanför scope

Skriv inte detta nu:

- Förvarning baserad på publicerade men ospelade trupper
- C-laget och Division 5 Sydvästra
- Dam- och juniorlag
- Cup- och träningsmatcher
- Val av säsong i gränssnittet
- Användarkonton, roller, notifieringar, statistik utöver låsstatus

---

## 11. Att bygga i ordning

1. Konfiguration och databasschema
2. iBIS-klient med typade svar och test mot sparad exempel-JSON
3. Synkjobb som fyller `matches` och `appearances`
4. Koppla in `eligibility.py`, verifiera att testerna går
5. Backend-API som serverar den beräknade listan
6. Frontend: listvyn i skrivskyddat läge
7. Lösenordsgrind
8. Redigeringsläge och overrides
9. Deploy

Efter steg 4 ska resultatet gå att jämföra mot vad tränarna räknat fram för hand.
Gör den jämförelsen innan något UI byggs.
