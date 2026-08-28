# Projektregler

- Läs SPEC.md innan du ändrar något.
- eligibility.py är färdig och testad. Skriv aldrig om regellogiken.
  All regelberäkning går genom compute_statuses().
- Kör `pytest` efter varje ändring. Alla 18 tester i test_eligibility.py
  måste gå igenom.
- Hemligheter läses från miljövariabler. Aldrig i koden, aldrig i git.
- Svenska i allt som användaren ser. Sentence case, inga utropstecken.
- Bygg bara det steget jag ber om. Fråga innan du går vidare.
- Felmeddelanden i UI måste spegla vad som faktiskt gick fel. Visa aldrig
  ett specifikt fel (fel lösenord, synk misslyckades) när orsaken kan vara
  ett nätverksfel eller ett annat statuskodsvar.
  