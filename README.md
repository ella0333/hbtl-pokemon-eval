# Human Before the Loop | Pokemon Eval

[![MIT License](https://img.shields.io/badge/MIT_License-869b9f?style=flat)](LICENSE)

**A Pokemon Red and Blue benchmark from the paper Human Before the Loop, where any
model plays the game through a multiple-choice menu.**

## How it works

The agent is presented with a multiple-choice menu and generates a JSON output
constrained to those choices. Final menu choices are bound to pre-written deterministic
Python scripts that execute the intended action, so walking to an object, talking to it
and advancing the dialogue all happen in code. This simplifies the decisions the agent
makes, pairing the classification strengths of LLMs with the reliability and
predictability of well-developed code.

In the paper, Muse Glimmer 30B and Muse Spark 1.2 played Pokemon Red until they received
the Pokedex, with no gameplay instructions and a 1,000-call limit, and got there in 342
and 58 calls.

## Quick start

```bash
git clone https://github.com/ella0333/hbtl-pokemon-eval.git
cd hbtl-pokemon-eval
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python setup.py    # model, game and run settings, then downloads the emulator
python run.py
```

The benchmark runs on Windows, and you need your own Pokemon Red or Blue ROM (the USA,
Europe release). Setup checks the file and downloads everything else.

Press `Ctrl+C` to save and pause a run. Running `python run.py` again offers to resume it.

Need help choosing a compatible model? See the **[Model Options Guide](docs/models.md)**

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

This project is licensed under the [MIT License](LICENSE). Setup downloads the following
components, which are not part of this repo and keep their own terms:

| Component | License | Details |
|-----------|---------|---------|
| **[VBA-RR](https://github.com/TASEmulators/vba-rerecording)** | GPL v2 | The Game Boy emulator, release 480LRC4 |
| **[pokemon-access](https://github.com/nuive/pokemon-access)** | None published | Screen reader scripts the Lua bridge reads the game through, commit 2deb98f (version 3.1.0) |
| **[pret/pokered](https://github.com/pret/pokered)** | None published | The disassembly the map database is built from, at a pinned commit |

Pokemon is a trademark of Nintendo, Creatures and Game Freak. This repo contains no ROMs.

---

[![MIT License](https://img.shields.io/badge/MIT_License-869b9f?style=flat)](LICENSE)
