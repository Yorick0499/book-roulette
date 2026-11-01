import argparse

from waitress import serve

from . import create_app


def main():
    parser = argparse.ArgumentParser(description="Book Roulette — lokalna biblioteka")
    parser.add_argument("--lan", action="store_true", help="Udostępnij w sieci LAN")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port musi być liczbą od 1 do 65535.")
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    if args.lan:
        print("Tryb LAN: urządzenia w sieci mogą uzyskać dostęp do prywatnej biblioteki.", flush=True)
    print(f"Book Roulette: nasłuchiwanie na {host}:{args.port}", flush=True)
    serve(create_app(), host=host, port=args.port, threads=4)


if __name__ == "__main__":
    main()
