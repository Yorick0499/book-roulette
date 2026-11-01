import argparse
from pathlib import Path

from waitress import serve

from . import create_app
from .db import get_db
from .importer import ImportError, import_books, load_demo


def main():
    parser = argparse.ArgumentParser(description="Book Roulette — lokalna biblioteka")
    parser.add_argument("--lan", action="store_true", help="Udostępnij w sieci LAN")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--demo", action="store_true", help="Oddzielna baza z fikcyjnymi książkami")
    parser.add_argument("--import-csv", type=Path, metavar="PLIK", help="Zaimportuj własny eksport Goodreads przed uruchomieniem")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port musi być liczbą od 1 do 65535.")
    if args.demo and args.import_csv:
        parser.error("Nie łącz --demo z --import-csv. Własne książki importuj do osobnej biblioteki.")
    config = None
    if args.demo:
        config = {"DATABASE": str(Path(__file__).resolve().parent.parent / "instance" / "demo.sqlite3")}
    app = create_app(config)
    with app.app_context():
        if args.demo:
            load_demo(get_db())
            print("Tryb demonstracyjny: oddzielna baza instance/demo.sqlite3.", flush=True)
        elif args.import_csv:
            try:
                with args.import_csv.open("rb") as stream:
                    content = stream.read(app.config["MAX_CONTENT_LENGTH"] + 1)
                if len(content) > app.config["MAX_CONTENT_LENGTH"]:
                    parser.error("CSV przekracza limit 10 MB.")
                result = import_books(get_db(), content)
            except OSError:
                parser.error("Nie można odczytać wskazanego pliku CSV.")
            except ImportError as error:
                parser.error(str(error))
            print(f"Import: dodano {result['added']}, zaktualizowano {result['updated']}, pominięto {result['skipped']}.", flush=True)
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    if args.lan:
        print("Tryb LAN: urządzenia w sieci mogą uzyskać dostęp do prywatnej biblioteki.", flush=True)
    print(f"Book Roulette: nasłuchiwanie na {host}:{args.port}", flush=True)
    serve(app, host=host, port=args.port, threads=4)


if __name__ == "__main__":
    main()
