import argparse
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 2000
DEBUG_HOST = "127.0.0.1"


def resolve_host(debug: bool) -> str:
    if debug:
        return DEBUG_HOST
    return os.getenv("HOST") or DEFAULT_HOST


def resolve_port() -> int:
    raw = os.getenv("PORT")
    if not raw:
        return DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"Invalid PORT in .env: {raw!r} (expected a number)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the FastAPI server.")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Bind to localhost and enable auto-reload on code changes.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Port to listen on (default: $PORT or {DEFAULT_PORT}).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    uvicorn.run(
        "server:app",
        host=resolve_host(args.debug),
        port=args.port if args.port is not None else resolve_port(),
        reload=args.debug,
    )
