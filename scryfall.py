import asyncio
import time
from collections import OrderedDict
from urllib.parse import urlparse
from uuid import UUID

import aiohttp

# This import matches your layout: models.py and scryfall.py in the same folder.
from models import Card, UserError


class Scryfall:
    BASE = "https://api.scryfall.com"

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        # Cache successful responses; each entry is (expiration_time, response_data).
        self._cache = OrderedDict()
        # Only one task at a time can enter the protected request section.
        self._lock = asyncio.Lock()
        self._next_request = 0.0
        # Clock reading when a rate-limit cooldown ends.
        self._blocked_until = 0.0

    async def _get(self, path: str, params=None, ttl=3600):
        """Retrieve cached or fresh JSON data from Scryfall.

        path: Endpoint such as /cards/named, or a full next-page URL.
        params: Optional query parameters, such as {"exact": "Kaalia of the Vast"}.
        ttl: Cache lifetime in seconds; defaults to one hour.
        """
        url = path if path.startswith("https://") else self.BASE + path
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "api.scryfall.com":
            raise UserError("Scryfall returned an unexpected pagination URL.")

        # Identify this exact URL and set of query parameters in the cache.
        key = (url, tuple(sorted((params or {}).items())))

        async with self._lock:
            now = time.monotonic()
            cached = self._cache.get(key)

            # Return an existing response if it has not expired.
            if cached and cached[0] > now:
                self._cache.move_to_end(key)
                return cached[1]

            # Stop new requests while the API rate-limit cooldown is active.
            if now < self._blocked_until:
                raise UserError("Scryfall is rate-limiting requests. Please try again later.")

            # Actually wait before sending the next request; keep this line active.
            await asyncio.sleep(max(0, self._next_request - now))
            self._next_request = time.monotonic() + 0.12

            try:
                async with self.session.get(url, params=params) as response:
                    if response.status == 429:
                        # Read the suggested cooldown, with a minimum of 30 seconds.
                        try:
                            delay = max(30.0, float(response.headers.get("Retry-After", 30)))
                        except ValueError:
                            delay = 60.0
                        self._blocked_until = time.monotonic() + delay
                        raise UserError(
                            "Scryfall is rate-limiting requests. Please try again later."
                        )

                    if response.status == 404:
                        raise UserError(
                            "No exact card match was found. Select a suggested full "
                            "card name and try again."
                        )

                    if response.status != 200:
                        raise UserError(
                            "Scryfall couldn't complete that lookup. Try again shortly."
                        )

                    data = await response.json()

            # Handle connection problems, timeouts, and invalid JSON.
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                raise UserError(
                    "Scryfall couldn't be reached or returned an invalid response. "
                    "Try again shortly."
                ) from exc

            # Store the successful response and mark it as recently used.
            self._cache[key] = (time.monotonic() + ttl, data)
            self._cache.move_to_end(key)

            # Remove the least recently used entries when the cache is full.
            while len(self._cache) > 1024:
                self._cache.popitem(last=False)
            return data

    async def autocomplete(self, text: str) -> list[str]:
        """Return up to 20 suggested names after at least two characters."""
        text = text.strip()
        if len(text) < 2:
            return []
        data = await self._get("/cards/autocomplete", {"q": text}, ttl=300)
        return data.get("data", [])[:20]

    async def exact(self, name: str) -> Card:
        """Look up an exact name and convert its response into a Card object."""
        return Card.from_api(await self._get("/cards/named", {"exact": name.strip()}))

    async def by_id(self, card_id: str) -> Card:
        """Validate and look up a specific printing's Scryfall UUID."""
        try:
            UUID(card_id)
        except ValueError as exc:
            raise UserError(
                "Select a printing from the suggestions, or leave printing empty."
            ) from exc

        return Card.from_api(await self._get(f"/cards/{card_id}"))

    async def printings(self, card: Card) -> list[Card]:
        """Collect eligible English paper printings, newest releases first."""
        path = "/cards/search"
        params = {
            "q": f"oracleid:{card.oracle_id} game:paper lang:en",
            "unique": "prints",              # Return distinct printings.
            "order": "released",             # Sort by release date.
            "dir": "desc",                   # Put newest releases first.
            "include_extras": "true",        # Include normally omitted extras.
            "include_variations": "true",    # Include normally omitted variants.
        }
        result = []

        # Fetch all pages of results, validating each card as we go.
        while path:
            data = await self._get(path, params)
            for raw in data.get("data", []):
                try:
                    printing = Card.from_api(raw)
                except UserError:
                    continue

                # Make sure the printing belongs to the requested card.
                if printing.oracle_id == card.oracle_id:
                    result.append(printing)

            path = data.get("next_page") if data.get("has_more") else None
            # Next-page URLs already contain their query parameters.
            params = None
        return result

    async def selected(self, name: str, printing_id: str | None = None) -> Card:
        """Use the requested printing, or select the newest eligible one."""
        card = await self.exact(name)

        if printing_id:
            selected = await self.by_id(printing_id)
            if selected.oracle_id != card.oracle_id:
                raise UserError(
                    "The selected printing does not match the requested card name."
                )
            return selected
        
        #No Printing is selected so we will choose the newest eligible printings
        printings = await self.printings(card)
        
        if not printings:
            raise UserError(
                "No eligible English paper printings were found for this card."
            )
            
        return printings[0] #Returns the newest eligible printing