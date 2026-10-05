import os
import discord
from discord import app_commands
from database import init_db
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

init_db()
bot.run(TOKEN)
