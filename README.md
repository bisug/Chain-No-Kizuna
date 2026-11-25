# Chain No Kizuna

A Telegram word chain bot built with aiogram. It provides ten game modes, stores active games in Redis, and stores statistics and game archives in MongoDB. Multiple bot instances use a Redis lock so only one polls Telegram.

This is a fork of [jonowo/on9wordchainbot](https://github.com/jonowo/on9wordchainbot).

## Requirements

- Python 3.14 or newer. The repository pins 3.14.10 in `.python-version`.
- [uv](https://docs.astral.sh/uv/) for dependency management.
- MongoDB and Redis or Valkey.
- A Telegram bot token from [@BotFather](https://t.me/BotFather).

## Install

```bash
git clone https://github.com/bisug/Chain-No-Kizuna.git
cd Chain-No-Kizuna
uv sync --frozen
cp .env.template .env
```

Set `TOKEN`, `MONGO_URI`, `REDIS_URL`, and `OWNER_ID` in `.env`. These variables have no default; the bot refuses to start without them.

## Run

```bash
uv run python -m chainnokizuna
```

The Dockerfile builds a non-root image that runs the same entry point. `heroku.yml` deploys that image as a worker.

## Configuration

Configuration comes from environment variables. See `.env.template` for the full list.

| Variable | Description | Default |
| --- | --- | --- |
| `TOKEN` | Telegram bot token. Required. | none |
| `MONGO_URI` | MongoDB URI. `MONGODB_URI` is accepted as an alias. Required. | none |
| `REDIS_URL` | Redis or Valkey URL. Required. | none |
| `OWNER_ID` | Numeric Telegram user ID. Required. | none |
| `VP_TOKEN` | Token for the optional virtual player bot. | unset |
| `DB_NAME` | MongoDB database name. | `WordChainDB` |
| `ADMIN_GROUP_ID` | Group for admin reports. `0` disables reports; `/feedback` needs a real group ID. | `0` |
| `OFFICIAL_GROUP_ID` | Group that receives the welcome message. `0` disables it. | `0` |
| `WORD_ADDITION_CHANNEL_ID` | Channel for word addition announcements. Set a real channel ID; `0` is invalid. | `0` |
| `SUPPORT_GROUP` | Support group username, without `@`. | `SuMelodyVibes` |
| `UPDATE_CHANNEL` | Update channel username, without `@`. | `SuMelodyVibes` |
| `VIP` | Comma-separated or JSON list of user IDs allowed to start Mixed Elimination. | `[]` |
| `VIP_GROUP` | Comma-separated or JSON list of group IDs allowed to start Mixed Elimination. | `[]` |
| `LOG_LEVEL` | DEBUG, INFO, WARNING, ERROR, or CRITICAL. | `INFO` |

Mixed Elimination is available when the user is in `VIP` or the chat is in `VIP_GROUP`.

## Commands

Add the bot to a group and use these commands. `/start<mode>` starts a game in that group.

| Command | Mode |
| --- | --- |
| `/startclassic` | Classic word chain. Also `/startgame`. |
| `/starthard` | Hard Mode. |
| `/startchaos` | Chaos mode, where the next player is chosen at random. |
| `/startcfl` | Chosen First Letter. |
| `/startrfl` | Random First Letter. |
| `/startbl` | Banned Letters. |
| `/startrl` | Required Letter. |
| `/startelim` | Elimination. |
| `/startmelim` | Mixed Elimination. Requires `VIP` or `VIP_GROUP`. |
| `/startguess`, `/new` | Guess the Word. |

Gameplay commands: `/join`, `/flee`, `/extend`, `/addvp`, `/remvp`.

Statistics: `/stats`, `/groupstats`, `/globalstats`, `/top` (also `/leaderboard`).

Information: `/help`, `/gameinfo`, `/ping`, `/runinfo`, `/chatid`, `/troubleshoot`, `/feedback`. `/ping` reports latency and CPU/RAM usage. `/runinfo` reports build time, uptime, dictionary size, and active game and player counts. `/feedback` forwards messages to `ADMIN_GROUP_ID`.

Dictionary: `/exists <word>`, `/reqaddword`.

Owner and admin commands: `/end`, `/forcestart`, `/forceskip`, `/forcejoin`, `/forceflee`, `/killgame`, `/maintmode`, `/addword`, `/rejword`, `/leave`, `/playinggroups`. `/db` (alias `/mongo`) runs a limited MongoDB `find` or `count` query; it does not inspect Redis.

## Word list

`chainnokizuna/data/wordlist.txt` ships with the bot. Validation uses a DAWG built from that file plus words accepted from the `wordlist` collection in MongoDB. MongoDB must be reachable at startup. Redis is required for leader election and game persistence; a failed Redis connection is logged.

The bot picks its own words from `chainnokizuna/data/commonwords.json`. If the pool has no match for a required prefix, it falls back to the full list. Validation always uses the full list, so the pool never narrows accepted player input. `chainnokizuna/data/fiveletters.json` is loaded but no current game mode uses it.

Rebuild the bundled data with:

```bash
uv run python tools/build_wordlist.py
```

## Development

The test suite uses only `unittest`. `tests/__init__.py` sets placeholder values for the four required variables before importing the application, so no external services are needed to start the suite. One test in `tests/test_leader.py` needs a live Redis server and is skipped unless `RUN_REDIS_TESTS=1` is set.

```bash
uv sync --frozen
uv run python -m unittest discover -s tests -t . -v
uvx ruff@0.17.6 check --no-cache --select F,E9 chainnokizuna config.py tests
uvx ruff@0.17.6 format --check .
```

CI runs the tests and the ruff check on pushes and pull requests to `main`.

## Deployment

- `Dockerfile`: multi-stage build, non-root runtime user.
- `heroku.yml`: deploys the Dockerfile as a Heroku worker.
- `Procfile`: `worker: python -m chainnokizuna`.

`app.json` declares the `heroku/python` buildpack, which does not match the Docker-only setup. Use `heroku.yml` for container deployment.

## License

[MIT](LICENSE).
