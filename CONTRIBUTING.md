# Współtworzenie Book Roulette

Dziękujemy za pomoc! Projekt pozostaje niewielką aplikacją lokalną:
Flask, SQLite, Jinja2 i Vanilla JavaScript, bez usług wymaganych do działania.

## Zmiany

1. Otwórz issue dla większej funkcji; małe poprawki można zgłaszać bezpośrednio jako pull request.
2. Przygotuj środowisko według README i dodaj test zachowania, które zmieniasz.
3. Uruchom testy backendu i `node tests/wheel-math.test.cjs`; przy zmianach interfejsu również `tests/browser_smoke.py`.
4. W pull requeście opisz problem, zmianę i wykonane testy.

Zachowuj polski język interfejsu, dostępność z klawiatury, lokalne zasoby
i obsługę `prefers-reduced-motion`. Losowanie musi pozostać niezależne od animacji.
Nie dodawaj scrapingu Goodreads ani nieudokumentowanych endpointów.

## Prywatność

Używaj wyłącznie fikcyjnych danych testowych. Nie commituj CSV użytkowników,
baz, kluczy, `.env`, logów z danymi ani prywatnych zrzutów ekranu.
Przed wysłaniem zmian sprawdź `git diff --cached` — również dla nowych plików.
Nie publikuj sekretów w issue; w razie ujawnienia unieważnij je u dostawcy.
