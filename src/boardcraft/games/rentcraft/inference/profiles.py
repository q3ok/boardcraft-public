"""The two closed, versioned input profiles supported by the exported policies."""
from dataclasses import dataclass

MINI = "rentcraft-legacy-blocks-mini-evo5"
V7 = "rentcraft-visible-v7-offers8"


@dataclass(frozen=True)
class ModelProfile:
    id: str
    version: int
    size: int
    candidates: bool

    def encoder(self):
        if self.id == MINI:
            from .encoding_blocks import MiniEncoder
            return MiniEncoder()
        from .encoding_v7 import V7Encoder
        return V7Encoder()

    def action_space(self):
        from .tradebuilder import TradeBuilderActionSpaceV2
        space = TradeBuilderActionSpaceV2()
        space.candidates = self.candidates
        return space

    def identities(self):
        """Fixed local format binding, separate from the shared Dice descriptor."""
        from boardcraft.inference.spec import digest
        from boardcraft.registry import get_game
        from .tokens import token_layout
        from dataclasses import asdict
        encoder = self.encoder()
        tokens = token_layout(self.id)
        descriptor = {"id": self.id, "version": self.version, "size": self.size,
                      "blocks": list(encoder.layout.items()),
                      "players": tokens.player_idx.tolist(), "spaces": tokens.space_idx.tolist(),
                      "globals": [(name, ix.tolist()) for name, ix in tokens.globals]}
        if self.id == V7:
            descriptor.update(fields=asdict(tokens.fields), field_card=tokens.field_card.tolist())
        module = get_game("rentcraft")
        return {"game_id": "rentcraft", "rules_version": module.rules_version,
                "observation_version": module.observation_version,
                "encoder_id": self.id, "encoder_version": self.version,
                "encoder_layout_hash": digest(descriptor),
                "action_id": "rentcraft-standard-v2", "action_version": 2,
                "action_layout_hash": digest({"id": "rentcraft-standard-v2", "version": 2, "size": 221}),
                "input_size": self.size, "output_size": 221}


def resolve_profile(profile_id, version=1):
    if type(version) is not int or version != 1 or profile_id not in (MINI, V7):
        raise ValueError("Unknown Rentcraft model input profile or version")
    return ModelProfile(profile_id, 1, 1055 if profile_id == MINI else 3734, profile_id == MINI)
