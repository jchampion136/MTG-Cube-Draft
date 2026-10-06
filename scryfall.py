import asyncio
import time
from collections import OrderedDict
from urllib.parse import urlparse
from uuid import UUID

import aiohttp

from .models import Card, UserError


class Scryfall:
    BASE = "https://api.scryfall.com"

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self._cache = OrderedDict() //Create empty cache for failed fetched responses.
        self._lock = asyncio.Lock() //Creates a lock to only allow one coroutine to process at a time
        self._next_request = 0.0
        self._blocked_until = 0.0

    //Shared function for retrieving JSON data from Scryfall 
    async def _get(self, path: str, params=None, ttl=3600):
        url = path if path.startswith("https://") else self.BASE + path
        if urlparse(url).scheme != "https" or urlparse(url).netloc != "api.scryfall.com":
            raise UserError("Scryfall returned an unexpected pagination URL.")
        key = (url, tuple(sorted((params or {}).items())))
        async with self._lock:
            now = time.monotonic()
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                self._cache.move_to_end(key)
                return cached[1]
            if now < self._blocked_until:
                raise UserError("Scryfall is rate-limiting requests. Please try again later.")
            await asyncio.sleep(max(0, self._next_request - now))
            self._next_request = time.monotonic() + 0.12

            try:
              async with self.session.get(url, params=params) as response:
                if response.status == 429:
                  try:
                    delay = max(30, float(response.headers.get("Retry-After", 30)),)
                  except ValueError:
                    delay = 60.0
                  self._blocked_until = time.monotonic() + delay
                  raise UserError("Scryfall is limiting requests. Please try again later.")
                if response.status == 404:
                  raise UserError("No card match found. Selected a real full card name and try again.")
                  
                if response.status != 200:
                  raise UserError("Scryfall couldn't complete the lookup. Try again later.")

                data = await response.json()
                
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
              raise UserError("Scryfall couldn't be reached right now, Please try again later.") from exc
              
            self._cache[key] = (time.monotonic() + ttl, data)
            self._cache.move_to_end(key)
            while len(self._cache) > 1024:
                self._cache.popitem(last=False)
            return data

    async def autocomplete(self, text: str) -> list[str]:
        text = text.strip()
        if len(text) < 2:
            return []  # Our UX policy; avoids extremely broad queries.
        data = await self._get("/cards/autocomplete", {"q": text}, ttl=300)
        return data.get("data", [])[:20]

    async def exact(self, name: str) -> Card:
        return Card.from_api(await self._get("/cards/named", {"exact": name.strip()}))

    async def by_id(self, card_id: str) -> Card:
      try:
        UUID(card_id)
      except ValueError as exc:
        raise UserError("Select a printing from the suggestions, or leave printing empty.") from exc

      return Card.from_api(await self._get(f"/cards/{card_id}"))

    async def printings(self, card: Card) -> list[Card]:
        # Oracle query prevents punctuation/name query injection and includes alternate art.
        path = "/cards/search"
        params = {
            "q": f"oracleid:{card.oracle_id} game:paper lang:en",
            "unique": "prints",
            "order": "released",
            "dir": "desc",
            "include_extras": "true",
            "include_variations": "true",
        }
        result = []
        while path:
            data = await self._get(path, params)
            for raw in data.get("data", []):
                try:
                    printing = Card.from_api(raw)
                except UserError:
                    continue
                if printing.oracle_id == card.oracle_id:
                    result.append(printing)
            path = data.get("next_page") if data.get("has_more") else None
            params = None
        return result

    async def selected(self, name: str, printing_id: str | None = None) -> Card:
        card = await self.exact(name)
        if printing_id:
            selected = await self.by_id(printing_id)
            if selected.oracle_id != card.oracle_id:
                raise UserError("That printing belongs to a different card. Reselect the printing.")
            return selected
        printings = await self.printings(card)
        if not printings:
            raise UserError("No eligible English paper printing was found for this card.")
        return printings[0]
