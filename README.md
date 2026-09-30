# Run the Project

## Requirements

- Python 3.12+
- Jira Cloud credentials
- An Ollama Cloud or OpenAI-compatible API key

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

On macOS or Linux, activate the environment with:

```bash
source .venv/bin/activate
cp .env.example .env
```

Fill in the required values in `.env`, then start the app:

```powershell
python -m uvicorn chatbot_jira.api:app --host 0.0.0.0 --port 8000 --env-file .env
```

Open <http://localhost:8000>.

## Run with Docker

Copy `.env.example` to `.env`, fill in the required values, then run:

```powershell
docker compose up --build
```

Open <http://localhost:8000>. Stop the app with:

```powershell
docker compose down
```

## Run tests

```powershell
python -m pytest
```
