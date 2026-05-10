# Deployment Instructions (Render Free Tier)

## Best Free Domain

Use Render's free subdomain.

- Every Render web service gets a free `onrender.com` URL.
- This is the simplest no-cost domain option for this app.
- Example format: `https://your-service-name.onrender.com`

If you want a custom domain later, you can add one in Render without losing the `onrender.com` URL.

## What This Repo Already Includes

This repo already includes [render.yaml](/Users/nileshsule/project/real-estate-broker/real-estate-broker/render.yaml), which is enough for a free Render web service:

- Python web service
- Free plan
- Install command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health check: `/health`

## Deploy on Render

1. Push this repository to GitHub, GitLab, or Bitbucket.
2. Sign in to Render.
3. Click `New +` -> `Blueprint`.
4. Connect the repository.
5. Render will read `render.yaml`.
6. Confirm the service creation.
7. After the deploy finishes, open the generated `onrender.com` URL.

## Required and Optional Environment Variables

These can be set in Render during setup or later in the service dashboard.

- `ENVIRONMENT=production`
- `DATABASE_URL=sqlite:///./real_estate.db`
- `WHATSAPP_VERIFY_TOKEN`

Optional:

- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `FIELD_AGENT_NAME`
- `FIELD_AGENT_PHONE`

Notes:

- If `OPENROUTER_API_KEY` is empty, the app still starts, but LLM-backed replies will not work.
- `WHATSAPP_VERIFY_TOKEN` should be set to a private random value in Render before connecting Meta's webhook.

## WhatsApp Webhook Setup

After deployment, configure the Meta webhook URL as:

- `https://your-service-name.onrender.com/webhook/whatsapp`

Use the same value for `WHATSAPP_VERIFY_TOKEN` in both Meta and Render.

## Verify the Deploy

Check these endpoints after the first deploy:

- `GET /health`
- `GET /docs`

Example:

- `https://your-service-name.onrender.com/health`

## Important Free-Tier Limitations

- Free Render web services can spin down after inactivity, so the first request after idle time may be slow.
- This app uses SQLite by default. On Render free tier, that database is fine for demos, but it is not a durable production setup.
- If the service redeploys or the instance is replaced, SQLite data can be lost.

## Recommended Free-Tier Use

This setup is good for:

- demo deployments
- webhook testing
- sharing a live preview
- basic product validation

If you want persistent real data, move `DATABASE_URL` to a managed Postgres database later.
