# Book Roulette

Prywatna, lokalna aplikacja do wybierania kolejnej książki z biblioteki Goodreads.
Interfejs po polsku, Flask + SQLite + Jinja2, bez zewnętrznych kont i telemetrii.

## Stan: etap 2

Działa import CSV Goodreads z podsumowaniem, podgląd książek, liczniki, trwała baza
SQLite oraz serwer Waitress. Wyszukiwanie, filtry, wybór do losowania, koło i historia
w interfejsie będą wdrażane w kolejnych etapach. Obecna wersja nie jest jeszcze kompletną aplikacją.

## Instalacja (Ubuntu 24.04)

Wymagany Python 3.12+ z modułem `venv` i pip. Jeśli brakuje `venv`, zainstaluj
pakiet `python3-venv` narzędziami systemowymi przed instalacją projektu.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m book_roulette
```

Otwórz `http://127.0.0.1:8080`. Zatrzymaj serwer przez Ctrl+C.
Opcja `--port 8090` zmienia port. Aplikacja działa bez internetu.

## Dostęp w LAN i z iPhone'a

```bash
.venv/bin/python -m book_roulette --lan --port 8080
```

Połącz telefon i laptop z tą samą zaufaną siecią Wi-Fi. Znajdź adres LAN laptopa
(np. przez `hostname -I`) i otwórz w Safari `http://ADRES_IP_LAPTOPA:8080`.
Serwer WSGI Waitress działa bez debuggera. Domyślnie dostęp jest ograniczony
do localhost; `--lan` świadomie udostępnia serwer na wszystkich interfejsach.

Nie ma uwierzytelniania: urządzenia mające dostęp do portu mogą uzyskać dostęp
do prywatnej biblioteki i przyszłych operacji zapisu. LAN nie jest w pełni zaufany.
Nie przekierowuj portu na routerze ani nie wystawiaj aplikacji w internecie.
Program nie zmienia zapory UFW. W sieci gościnnej izolacja urządzeń może blokować
połączenie. Nie deklarujemy testów na fizycznym iPhonie ani obsługi PWA.

## Dane i architektura

- `book_roulette/__init__.py`: fabryka aplikacji i strona startowa.
- `book_roulette/db.py`, `schema.sql`: połączenie i niedestrukcyjna inicjalizacja SQLite.
- `book_roulette/templates/`, `static/`: lokalny interfejs, bez CDN.
- `instance/library.sqlite3`: prywatne dane, wyłączone z Git; kopiuj przy zatrzymanym serwerze, aby wykonać kopię zapasową.
- `tests/`: testy z izolowaną bazą tymczasową.

Model rozdziela lokalny `books.id` i opcjonalny unikalny `goodreads_id`.
Przechowuje metadane eksportu i lokalny wybór; historia posiada kopię tytułu
i autora oraz referencję do książki, a ustawienia osobną tabelę klucz–wartość.
Restart serwera nie usuwa danych. Inicjalizacja tworzy wyłącznie brakujące tabele;
przyszłe zmiany schematu będą wymagały migracji.

## Goodreads

Eksport: **My Books → Import and Export → Export Library**. Następnie wybierz
**Importuj CSV** w aplikacji. Obsługiwany jest CSV UTF-8 (również z BOM), z nagłówkami
`Book Id`, `Title`, `Author`; pozostałe pola mogą być puste lub nieobecne.
Limit całego żądania to 10 MB i 50 000 wierszy. Błędna struktura pliku odrzuca cały
import. Wiersze bez liczbowego Book Id, bez tytułu, z błędną oceną oraz duplikaty
w pliku są pomijane; pierwszy poprawny wiersz danego ID wygrywa.
Ponowny import aktualizuje metadane bez zmiany lokalnego wyboru, historii i ustawień.
Nie usuwa książek nieobecnych w pliku. Import nie zaznacza automatycznie książek.
Odświeżenie podsumowania nie powtarza importu. Projekt nie używa API Goodreads,
scrapingu ani prywatnych endpointów. Okładki z zewnętrznych usług są opcjonalnym
elementem późniejszego etapu; obecnie aplikacja nie wysyła danych książek do sieci.

## Testy

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Testy używają wyłącznie fikcyjnych danych. Sprawdzają import, Unicode, ISBN,
duplikaty, ponowny import, zachowanie danych lokalnych, błędne pliki, CSRF,
limit przesyłania, bezpieczne wyświetlanie tytułów i trwałość bazy.

## Publiczne repozytorium, prywatne dane

Nie commituj eksportów Goodreads, baz, kluczy, plików `.env` ani kopii zapasowych.
`.gitignore` wyklucza te pliki; przed każdym commitem sprawdzaj `git diff --cached`.
Pliki CSV są przetwarzane w pamięci i nie są zapisywane na dysku.
Klucz podpisujący sesję jest generowany lokalnie w `instance/session.key`
z uprawnieniami 0600 i nigdy nie powinien trafić do Git. Nie usuwaj go podczas
zwykłego restartu. Cookies zawierają tylko token CSRF i liczby z podsumowania,
bez treści biblioteki. Formularze zapisu wymagają tokenu CSRF.
Tryb HTTP w LAN nie szyfruje transmisji; używaj wyłącznie zaufanej sieci.
Aplikacja nie ma logowania i CSRF nie zastępuje uwierzytelniania.
