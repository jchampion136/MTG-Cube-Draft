from dataclasses import dataclass
from typing import Any


class UserError(Exception):
    """An error that is caused by user input, not a bug in the code."""
    pass
  
@dataclass(frozen=True)
class Card: 
  scryfall_id: str
  oracle_id: str
  name: str
  set_name: str
  set_code: str
  collector_number: str
  raw: dict[str, Any]

  @classmethod
  def from_api(cls, data: dict[str, Any]) -> "Card":
    required = ("id", "oracle_id", "name", "set_name", "set", "collector_number")
      
    if any(field not in data for field in required):
        raise UserError(f"The card data is missing required information.")
    
    # Check if card is a physical card (not a digital-only card
    if (
      data.get("object") != "card"
      or "paper" not in data.get("games", [])
      or data.get("oversized", False)
      or data.get("layout") in {
        "token",
        "double_faced_token",
        "emblem",
        "art_series",
      }
    ):
      raise UserError(
        "Choose a real, playable paper Magic card, rather than a token, "
        "emblem, art card, oversized card, or digital-only card."
      )
    
    if not data.get("oracle_id"):
        raise UserError(f"Card does not have an oracle_id: {data}")
      
    return cls(
      scryfall_id = data["id"],
      oracle_id = data["oracle_id"],
      name = data["name"],
      set_name = data["set_name"],
      set_code = data["set"],
      collector_number = data["collector_number"],
      raw = data,
    )

  # Return a string that uniquely identifies this printing of the card.
  @property
  def printing(self) -> str:
    """Return a string that uniquely identifies this printing of the card."""
    return f"{self.set_name} ({self.set_code.upper()}) · #{self.collector_number}"
  
  @property
  def images(self) -> list[str]:
    """Image URLs for a normal card or each face of a double-faced card. Returns an empty list if no images are available."""
    image_uris = self.raw.get("image_uris")
    if image_uris:
      image = (
        image_uris.get("normal") 
        or image_uris.get("large") 
        or image_uris.get("small")
      )
      return [image] if image else []
      
    #Double-faced cards have a "card_faces" field instead of "image_uris"
    images = []
    for face in self.raw.get("card_faces", []):
      image_uris = face.get("image_uris")
      if image_uris:
        image = (
          image_uris.get("normal") 
          or image_uris.get("large") 
          or image_uris.get("small")
        )
        if image:
          images.append(image)
    return images
  
  @property
  def rules(self) -> str:
    "Build readable rules text from the card's oracle text and any flavor text."
    def describe(face: dict[str, Any]) -> str:
      lines = []
      
      for field in ("name", "type_line", "oracle_text", "flavor_text"):
        value = face.get(field)
        if value:
          lines.append(value)
      
      # Add power/toughness or loyalty if present
      if "power" in face and "toughness" in face:
        lines.append(f"{face['power']}/{face['toughness']}")
        
      if "loyalty" in face:
          lines.append(f"Loyalty: {face['loyalty']}")
      return "\n".join(lines)
    
    faces = self.raw.get("card_faces")
    if faces:
      return "\n\n".join(describe(face) for face in faces)
    return describe(self.raw)
    
      
