# Boardcraft

Boardcraft is a local board-game platform. You run a small server on your own
computer and play in the browser, or let an agent play through MCP. It includes
two games:

- **Dice Circuit**: a dice and score-sheet game for people, random and greedy
  bots, and compatible local NumPy policies. [Rules](src/boardcraft/games/dice_circuit/content/rules.en.md)
- **Rentcraft**: buy property, collect rent, bid in auctions and negotiate
  trades, with two to four seats, 35 built-in bots and three neural network
  opponents. [Rules](src/boardcraft/games/rentcraft/content/rules.en.md)

## Requirements

- Python 3.10 or newer
- A browser with WebGL enabled

## Install

Get the code with `git clone https://github.com/q3ok/boardcraft-public`
or download and extract an archive. Then, from the
project folder, create a virtual environment and install.

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install ".[serve]"
```

Windows (CMD):

```bat
python -m venv .venv
.venv\Scripts\activate.bat
pip install ".[serve]"
```

Linux and macOS:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install ".[serve]"
```

The `serve` extra includes the MCP dependency. The separate `mcp` extra installs
only MCP, for use without the web server.

## Run

```sh
boardcraft serve --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 in your browser, create a table and play. Your
browser remembers your seat, so you can return to a table later.

Tables are saved in a data directory. Without `--data-dir`, Boardcraft uses
`%LOCALAPPDATA%\Boardcraft` if that variable is set, otherwise
`$XDG_DATA_HOME/Boardcraft` if set, otherwise `~/.local/share/boardcraft`. Use
`--data-dir PATH` to choose another location. It must be outside the
application folder.

## Play against a neural network

Rentcraft includes three trained opponents: `mini-evo5`, `njord-gen0` and `hodr`.
They are installed with the game and run on NumPy, with no GPU or extra downloads.

1. In the lobby, choose **Rentcraft** under **New table**.
2. On a seat, choose **RL model**, then pick a model from the list.
3. Choose **Create and sit**.

## Agents and MCP

The server you started also speaks MCP over Streamable HTTP at
`http://127.0.0.1:8000/mcp`. For now it accepts loopback hosts only
(`127.0.0.1`, `localhost`, `[::1]`). A typical client configuration:

```json
{
  "mcpServers": {
    "boardcraft": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

The exact file and key names depend on your MCP client. Some clients ask you to
approve the Boardcraft tools before they can be used.

Alternatives:

- `boardcraft mcp` runs an MCP stdio server for clients that launch a command.
- `boardcraft mcp --server-url http://127.0.0.1:8000` runs stdio but relays to a
  server that is already running.

All of these use the same saved tables.

## Bots and local models

Available bots are listed in the lobby, at HTTP `GET /api/games/{game_id}/bots`,
and through the MCP `list_bots` operation. A table keeps the exact bot version
selected when it was created. Restoring a missing version requires that same
version; another model is never substituted. Completed games can be replayed
without it.

To add your own approved inference files, pass `--model-source MANIFEST` to
`boardcraft serve` and `boardcraft mcp`. Processes sharing a data directory must
use the same source inventory. The JSON manifest contains `version: 1` and a
`models` list; each entry has `choice_id`, `metadata` and `weights`. File paths
are local to the administrator and may be relative to the manifest. Model names
come from validated inference metadata. The included Rentcraft models are always
available and need no manifest.
