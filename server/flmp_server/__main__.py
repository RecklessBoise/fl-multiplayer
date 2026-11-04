"""Run the room server: `flmp-server` or `python -m flmp_server`.

Configuration comes from flags or environment variables (no secrets needed):
FLMP_HOST (default 0.0.0.0), PORT or FLMP_PORT (default 8765), FLMP_LOG_LEVEL.
TLS is expected to be terminated by the hosting platform (wss://).
FLMP_TRUST_PROXY=1 trusts the Fly-Client-IP header for per-IP limits: set it ONLY
behind Fly.io's proxy, never when the server is directly reachable (spoofable).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os

from flmp_server.app import Settings, start_server


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="flmp-server", description="FL Multiplayer room server")
    parser.add_argument("--host", default=os.environ.get("FLMP_HOST", "0.0.0.0"))  # noqa: S104
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT") or os.environ.get("FLMP_PORT") or 8765),
    )
    parser.add_argument("--log-level", default=os.environ.get("FLMP_LOG_LEVEL", "INFO"))
    return parser.parse_args(argv)


async def serve_forever(host: str, port: int) -> None:
    settings = Settings(trust_proxy_header=os.environ.get("FLMP_TRUST_PROXY") == "1")
    server, url = await start_server(settings, host=host, port=port)
    logging.getLogger("flmp.server").info("listening on %s", url)
    async with server:
        await server.serve_forever()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve_forever(args.host, args.port))


if __name__ == "__main__":
    main()
