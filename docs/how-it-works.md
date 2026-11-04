# How it works

## The game layer

The harness runs Pokemon Red or Blue in VBA-RR and loads a Lua bridge into the emulator.
The bridge reads the game's memory and the screen reader output from pokemon-access, and
writes the game state to a file every few frames. The game layer in
`src/hbtl_pokemon/game.py` turns that state into a short description and a lettered list
of choices.

Each turn the model picks a letter. Picking something from the Nearby list walks the
player there on a precomputed map, interacts with it, and advances the dialogue that
follows, and the dialogue text comes back in the next turn's description. Outdoors the
model can also walk North, South, East or West. Battles show the four battle commands,
then the moves or items, and the party list when a Pokemon has to be sent out. Naming
screens ask for a name of up to seven letters instead of a letter.

Some things always stay on because the game can't be played through the menu without
them: walking on the map database, dialogue advancing on its own, retrying a door that
didn't open, describing the Town Map in text (the real screen soft-locks the game), the
party and bag readout, the safety notes on the Pokemon storage PC, and the compass moves
outdoors.

## The context window

Each request is the system prompt, the last 24 messages, and the current game state.
After each decision the state is added to that history as a user message and the chosen
letter as the assistant message (or "Named: X" on a naming screen). The model's thinking
is never added back. Dialogue that arrives with no choices is added as a user message, cut
to 500 characters. This matches the constrained harness in the paper.

The system prompt is in `prompts/`. It changes between indoors and outdoors, and follows
the current area when location tips are on.

## Hints

Every hint group is off unless it is turned on in setup, and the setting is recorded with
each run.

| Group | What it adds |
|---|---|
| Navigation | The Directions line, the "already interacted", "you just exited here" and "blocked" marks, and the strategy lines in the prompt |
| Stuck notes | A note when a move changed nothing, and when a battle menu has been opened and closed over and over |
| Battle and catch tips | Catch chances on weak wild Pokemon, the PP and Struggle notes, and Poke Ball counts |
| Item descriptions | What each item does, next to it in menus |
| Location tips | A line about the current town or route in the system prompt |
| Story notes | The starter, Poke Ball permission, demonstration battle and resumed-game notes |

## Milestones

The run's target is one of the Pokedex from Oak, a number of badges, or the Hall of Fame.
The bridge reads three values from game memory, at addresses taken from the pret/pokered
disassembly: the event flag set when Oak hands over the Pokedex, the badge byte, and the
number of Hall of Fame teams. A milestone counts once two reads in a row show it, and
nothing the model says is taken into account.

When a milestone is reached the harness saves in-game at the next plain overworld screen
and keeps that save in the run folder's `saves/`, named after the milestone (for example
`badge_2.sav`). Runs for the leaderboard always start from a new game. Your own saves can be
used for your own runs through the continue and slot save modes.

## Saves, pausing and resuming

The save mode decides what carries over between runs: a new game every run, the same game
continued, or named save slots. The harness also saves in-game every 50 turns.
Only the game's own save is used, never emulator save states.

`Ctrl+C` pauses a run. The harness saves in-game at the next overworld screen and closes
the emulator. If the game is in a battle, it keeps playing until the battle is over;
pressing `Ctrl+C` a second time stops right away, and the run resumes from its last save.
The next `python run.py` offers to resume the paused run, which then continues the same
logs and totals. A run that crashed or whose window was closed can be resumed the same way,
and the calls it made after its last save still count. Time spent paused is not counted.

## The results folder

Each run writes `results/run_<date>_<model>/` with:

- `summary.json`: the model, endpoint, game, start, target, hints, outcome,
  milestones and the call each was reached on, calls, tokens, cost, wall time, the harness
  commit, and a SHA-256 hash of every other file
- `model_calls.jsonl`: every request and response, with token usage and cost
- `events.jsonl`: every turn's game state, choices, answer, result, position and memory reads
- `inputs.jsonl`: every button press, by frame
- `saves/`: the latest save and one per milestone

A run with a call cap of 5 works as a pilot. Every summary includes the average cost per
call and what the full call cap would cost at that rate.

## Scoring

A run is a success when its target milestone is read from memory before it reaches the
call cap. The leaderboard groups runs by game and target, since only runs of the same
stretch can be compared, and within each group lists successful runs first, then fewer
calls and lower cost. Every call the model answered counts, including answers that were
rejected and asked again.

Cost comes from the provider when it reports one (OpenRouter does). Otherwise it uses the
prices per million tokens in `config.yaml`, and a local model costs nothing.

## Troubleshooting

If VBA-RR shows an error about a missing DLL when it opens, install the DirectX
End-User Runtime, which the emulator needs. Keep the repo in a folder whose path uses plain
letters only, since the Lua bridge opens its files by path.
