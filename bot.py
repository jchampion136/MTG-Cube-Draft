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



