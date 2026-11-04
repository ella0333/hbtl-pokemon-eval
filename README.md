# Human Before the Loop | Pokemon Eval

[![MIT License](https://img.shields.io/badge/MIT_License-869b9f?style=flat)](LICENSE)

**A Pokemon Red and Blue benchmark for testing LLMs inside the Human Before the Loop
framework.**

Human Before the Loop designs an agent's action space and data access before runtime,
leaving no path open to data exfiltration or destructive actions. This method does not
trade task reliability or capability for the security improvements it guarantees.

This repo is a benchmark based on the Pokemon experimental setup from the paper. Any LLM
can be run through the same framework and compared.

## Quick start

The benchmark runs on Windows. Before you start, have your own Pokemon Red or Blue ROM
ready (the USA, Europe release), since setup asks for its location. Setup checks the file
and downloads everything else.

```bash
git clone https://github.com/ella0333/hbtl-pokemon-eval.git
cd hbtl-pokemon-eval
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python setup.py    # model, game and run settings, then downloads the emulator
python run.py
```

Press `Ctrl+C` to save and pause a run. Running `python run.py` again offers to resume it.

Looking for which models work? See the **[Model Options Guide](docs/models.md)** for
backends, requirements, and configuration.

## How it works

The LLM is presented with a multiple-choice menu and generates a JSON output constrained
to those choices. The menu system is designed as a Hierarchical Extended Finite State
Machine with nested submenus that lead to the final action through various LLM decisions.

Final menu choices are bound to pre-written deterministic Python scripts that execute the
intended action. This simplifies the decisions the LLM makes, pairing the classification
strengths of LLMs with the reliability and predictability of well-developed code, and
replaces the need for the model to string together complex tool calls directly.

## Model requirements

Any instruct model served through an OpenAI-compatible API (`/v1/chat/completions`) will
work, as long as it supports **structured output** (JSON schema responses) and **custom
system prompts**. Models without structured output are not compatible. Setup runs a
connection check at the end to confirm your model returns valid JSON, and auto-corrects
common issues (wrong model name format, unsupported parameters).

## Leaderboard

<!-- leaderboard:start -->
No runs have been submitted yet.
<!-- leaderboard:end -->

The full table is in [LEADERBOARD.md](LEADERBOARD.md).

## Submitting results

Each run writes a folder to `results/`. To submit one, fork this repo, copy the finished
run's folder into `submissions/`, and open a pull request. A check confirms that the logs
match the summary and that the milestone was read from game memory, and the run joins the
leaderboard once the pull request is merged.

## Docs

- **[How it works](docs/how-it-works.md):** the game layer, hints, milestones, the results folder and scoring
- **[Model options](docs/models.md):** which models and endpoints work, and why structured output is required

## Third-party licenses

This project is licensed under the [MIT License](LICENSE). It bundles the following
third-party components under their own licenses:

| Component | License | Details |
|-----------|---------|---------|
| **[VBA-RR](https://github.com/TASEmulators/vba-rerecording)** | GPL v2 | [vendor/VBA-RR/LICENSE-GPL-2.0.txt](vendor/VBA-RR/LICENSE-GPL-2.0.txt) · Source: [github.com/TASEmulators/vba-rerecording](https://github.com/TASEmulators/vba-rerecording) |
| **[LuaJIT](https://luajit.org/)** (lua51.dll) | MIT | [vendor/VBA-RR/LICENSE-LuaJIT.txt](vendor/VBA-RR/LICENSE-LuaJIT.txt) · Source: [luajit.org](https://luajit.org/) |

Setup downloads these, which are not part of this repo:

| Component | License | Details |
|-----------|---------|---------|
| **[pokemon-access](https://github.com/nuive/pokemon-access)** | None published | Screen reader scripts the Lua bridge reads the game through, commit 2deb98f (version 3.1.0) |
| **[pret/pokered](https://github.com/pret/pokered)** | None published | The disassembly the map database is built from, at a pinned commit |

Pokemon is a trademark of Nintendo, Creatures and Game Freak. This project is not
affiliated with or endorsed by Nintendo, Creatures or Game Freak, and this repo contains
no ROMs.

---

[![MIT License](https://img.shields.io/badge/MIT_License-869b9f?style=flat)](LICENSE)
