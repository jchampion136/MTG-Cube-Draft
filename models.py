from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class Card: 
  scryfall_id: str
  oracle_id: str
  name: str
  set_name: str
  collector_number: str
  raw: dict[str, Any]

  @classmethod
  def from_api(cls, data: dict[str, Any]) -> "Card":
