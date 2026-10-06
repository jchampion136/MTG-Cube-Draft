import logging
import os
import discord
import json
import math
import time
from dataclasses import dataclass

import aiohttp

from discord import app_commands
from database import init_db
from dotenv import load_dotenv

from .models import Card, UserError
from .repository import Repository
from .scryfall import Scryfall

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
settings = Settings(
        database_path=os.getenv("DATABASE_PATH", "data/cube.sqlite3"),
        guild_id=guild_id,
        confirmation_timeout=timeout,
        user_agent=os.getenv("SCRYFALL_USER_AGENT", Settings.user_agent),
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
bot = CubeBot(settings)
bot.run(token, log_handler=None)
init_db()

