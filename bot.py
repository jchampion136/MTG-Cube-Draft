"""Cube'd Phase 1: run this file beside models.py, repository.py, and scryfall.py."""

import asyncio
import json
import logging
import math
import os
from pathlib import Path
import time
from dataclasses import dataclass

import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv

from models import Card, UserError
from repository import Repository
from scryfall import Scryfall

log = logging.getLogger(__name__)

@dataclass(frozen=True)
class Settings:
    database_path: str = "data/cube.sqlite3"
    guild_id: int | None = None
    user_agent: str = "Cubed/0.1 (Discord community cube bot)"
    confirmation_timeout: int = 180

def is_admin(interaction: discord.Interaction) -> bool:
    """Check if the user is an admin."""
    user = interaction.user
    if isinstance(user, discord.Member):
        return user.guild_permissions.administrator or user.guild_permissions.manage_guild
    return False

def require_admin(interaction: discord.Interaction) -> None:
    """Raise an error if the user is not an admin."""
    if not is_admin(interaction):
        raise UserError("You need manage Server or Administrator permissions to use this command.")