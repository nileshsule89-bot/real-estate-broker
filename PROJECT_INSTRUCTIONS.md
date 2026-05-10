# Project Instructions

## Overview

This project is an AI-assisted real-estate broker backend built on FastAPI.
It handles:
- WhatsApp webhook message intake
- Requirement extraction (location, budget, BHK, rent/buy)
- Property search and weighted ranking
- Property shortlisting
- Visit scheduling
- Field agent assignment

## Tech Stack

- Backend: FastAPI
- Database: SQLite (default, configurable via `DATABASE_URL`)
- HTTP client: `httpx` (for WhatsApp Cloud API calls)
- ORM: SQLAlchemy
- LLM (optional): OpenRouter Chat Completions API

## Local Setup

1. Create and activate a Python virtual environment.
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Copy environment template:
   - `cp .env.example .env`
4. (Optional) Add OpenRouter credentials in `.env`:
   - `OPENROUTER_API_KEY=<your_key>`
   - `OPENROUTER_MODEL=openai/gpt-4o-mini`
5. Run the API:
   - `uvicorn app.main:app --reload`

The server will be available at `http://127.0.0.1:8000`.

## API Endpoints

- `GET /health`
  - Basic readiness endpoint.
- `GET /webhook/whatsapp`
  - WhatsApp webhook verification endpoint.
- `POST /webhook/whatsapp`
  - Receives incoming messages and returns/forwards reply.
- `POST /simulate-message`
  - Local testing endpoint without WhatsApp integration.

## Quick Functional Test

Use local simulation:

1. Send intent:
   - `POST /simulate-message` with JSON:
   - `{"from":"919999999999","message":"Looking for 2 BHK for rent in Thane under 25000"}`
2. Send shortlist:
   - `{"from":"919999999999","message":"shortlist <property_id>"}`
3. Schedule:
   - `{"from":"919999999999","message":"schedule my visits"}`

## Notes

- The app seeds sample properties and one default field agent on startup.
- WhatsApp sending is optional; if credentials are missing, replies are still generated and returned in API response.
- LLM is optional. If OpenRouter is configured, it helps with preference extraction and conversational clarification.
