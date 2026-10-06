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
        self._blocked_until = 0.0 //Stores clock time when a rate-limit cooldown ends

    /*Shared function for retrieving JSON data from Scryfall
        path: str - determines which endpoint to request such as "/cards/named"
        params=None — optional query parameters, such as {"exact": "Kaalia of the Vast"}.
        ttl=3600 — how many seconds to keep the response cached; (defaults to one hour).
    */
    async def _get(self, path: str, params=None, ttl=3600):
        url = path if path.startswith("https://") else self.BASE + path
        if urlparse(url).scheme != "https" or urlparse(url).netloc != "api.scryfall.com":
            raise UserError("Scryfall returned an unexpected pagination URL.")
        key = (url, tuple(sorted((params or {}).items())))
        /*Create Cache key to identify request
        e.g. url = "https://api.scryfall.com/cards/named"
            params = {"exact": "Kaalia of the Vast", "set": "c17"}
        */
        async with self._lock: //Session waits until another session completes
            now = time.monotonic() //Gets a clock reading in s.
            cached = self._cache.get(key) //Returns saved entry in cache, or None if none exists
            
            //Checks if entry exists and hasnt expired
            if cached and cached[0] > now:
                self._cache.move_to_end(key)
                return cached[1]

            //If cache request fail, check whether a rate-limit cooldown is active, stop lookup and throw an error
            if now < self._blocked_until:
                raise UserError("Scryfall is rate-limiting requests. Please try again later.")
            //await asyncio.sleep(max(0, self._next_request - now))
            self._next_request = time.monotonic() + 0.12 //Sets the Earliest time for the next request to 120 milliseconds.

            //Sends the request to Scryfall and handles failures
            try:
              async with self.session.get(url, params=params) as response:
                if response.status == 429: //Recieving too many requests
                  try:
                    delay = max(30, float(response.headers.get("Retry-After", 30)),)
                  except ValueError:
                    delay = 60.0
                  //Records when the cooldown ends and sends a message
                  self._blocked_until = time.monotonic() + delay
                  raise UserError("Scryfall is limiting requests. Please try again later.")
                if response.status == 404: //Card cant be matched.
                  raise UserError("No card match found. Selected a real full card name and try again.")
                  
                if response.status != 200: //Any other statuses not covered in other if blocks
                  raise UserError("Scryfall couldn't complete the lookup. Try again later.")

                data = await response.json()

            //Handles any Technical failures
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
              raise UserError("Scryfall couldn't be reached right now, Please try again later.") from exc

            //Saves response in the cache, limits the cache size and returns the data
            self._cache[key] = (time.monotonic() + ttl, data)
            self._cache.move_to_end(key)

            //Removes cache entries from beginning if there are more than 1024 entries
            while len(self._cache) > 1024:
                self._cache.popitem(last=False)
            return data

    //Auto complete queries from the user for card suggestions and show up to 20 cards
    async def autocomplete(self, text: str) -> list[str]:
        text = text.strip()
        if len(text) < 2:
            return []  # Our UX policy; avoids extremely broad queries.
        data = await self._get("/cards/autocomplete", {"q": text}, ttl=300)
        return data.get("data", [])[:20]

    //Looks up card by its exact name then returns a Card object containing the information
    async def exact(self, name: str) -> Card:
        return Card.from_api(await self._get("/cards/named", {"exact": name.strip()}))

    //Looks up specific card by its CardID
    async def by_id(self, card_id: str) -> Card:
        //Attempt to intepret string using the UUID
        try:
        UUID(card_id)
      except ValueError as exc:
        raise UserError("Select a printing from the suggestions, or leave printing empty.") from exc

      return Card.from_api(await self._get(f"/cards/{card_id}"))

    //Searches for different printings of the same card
    async def printings(self, card: Card) -> list[Card]:
        //Uses Scryfalls Search endpoint to build a Search query
        path = "/cards/search"
        params = {
            "q": f"oracleid:{card.oracle_id} game:paper lang:en",
            "unique": "prints",              //Returns distinct printings
            "order": "released",             //Sort by release date
            "dir": "desc",                   //put newest releases first
            "include_extras": "true",        //Include extra entries omitted from searches
            "include_variations": "true",    //Include variants omitted
        }
        result = [] //Creates empty list to collect Scryfall Responses

        //Collects eligible printings from Scryfall results
        while path: //Keeps looping while path contains an endpoint
            data = await self._get(path, params) //Detches the current page of search results
            for raw in data.get("data", []): //Goes through each card on page 
                //Validates the card and converrts it into Card Object
                try:
                    printing = Card.from_api(raw)
                except UserError:
                    continue
                //Checks if printing belongs to requested card. If so, add it to results list
                if printing.oracle_id == card.oracle_id:
                    result.append(printing)
            path = data.get("next_page") if data.get("has_more") else None
            params = None
        return result //Returns completed list of eligible printings

    //Selects which printing to use. The user can choose one or leave one by default.
    async def selected(self, name: str, printing_id: str | None = None) -> Card:
        card = await self.exact(name) //Looks up card by its exact name

        //If a user supplied a printing tetch that specific print
        if printing_id:
            selected = await self.by_id(printing_id)
            if selected.oracle_id != card.oracle_id:
                raise UserError("That printing belongs to a different card. Reselect the printing.")
            return selected //Returns the selected printing

        //If no printing is supplied, get the first eligible printing, sorted from newest first
        printings = await self.printings(card)
        if not printings:
            raise UserError("No eligible English paper printing was found for this card.")
        return printings[0]
