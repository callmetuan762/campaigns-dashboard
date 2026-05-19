"""Module entrypoint: `python -m src` -> asyncio.run(main())."""
import asyncio

from src.main import main

if __name__ == "__main__":
    asyncio.run(main())
