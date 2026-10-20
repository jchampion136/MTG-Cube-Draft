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

#Create Logger that reports connections errors etc.
log = logging.getLogger(__name__)

#Groups the database path 
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

def card_embeds(card: Card, footer: str) -> list[discord.Embed]:
    embed = discord.Embed(
        title=card.name[:256],
        url=card.raw.get("scryfall_uri"),
        description=card.rules[:3500],
        color=discord.Colour.blurple(),
    )
    
    embed.add_field(name="Printing", value=card.printing_label[:1024], inline=False)
    embed.set_footer(text=footer[:2048])
    images = [url for url in card.images if url]
    if images:
        embed.set_image(url=images[0])
    embeds = [embed]
    #DFC faces are separate images; adventure cards use the single full image
    for image in images[1:3]:
        face = discord.Embed(color=discord.Colour.blurple())
        face.set_image(url=image)
        embeds.append(face)
    return embeds

async def explain(interaction,message):
    message = message[:1900]
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
