Bundled play models use the version 1 manifest in models.json. Each entry has
choice_id, metadata and weights paths, relative to this directory. The default
bot catalog loads them automatically with the same validation as --model-source.

This distribution includes three Rentcraft opponents converted from the Rentcraft
project: mini-evo5, njord-gen0 and hodr. Each folder contains only the inference
weights (weights.npz) and minimal metadata (metadata.json); they play greedily
with NumPy and need no other dependencies.
