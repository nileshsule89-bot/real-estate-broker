1. 📌 Objective

Build an AI-powered agent that:

Interacts with users via WhatsApp
Understands property requirements conversationally
Fetches and ranks relevant properties
Shortlists based on user feedback
Schedules property visits
Assigns field agents for walkthroughs

2. 🧩 High-Level Architecture
User (WhatsApp)
      ↓
WhatsApp API (Meta Cloud API)
      ↓
Backend Orchestrator (FastAPI)
      ↓
Agent Layer (LLM + Tools)
      ↓
-----------------------------------------
| Search Service | Scheduling Service   |
| Property DB    | Agent Assignment     |
-----------------------------------------
      ↓
External Systems (internal DB/ web search)

3. 🧠 Agent Design
3.1 Agent Capabilities
Capability	Description
Intent Detection	Rent vs Buy, Budget, Location
Conversational Memory	Tracks preferences across messages
Property Search	Fetch from external/internal sources
Ranking	Score based on user preferences
Shortlisting	Store selected properties + web search
Scheduling	Coordinate visit slots
Assignment	Allocate field agent - Nilesh Sule - 9702044168

3.2 Agent Tools (Function Calling)

Your LLM agent should use structured tools:

[
  {
    "name": "search_properties",
    "input": {
      "location": "string",
      "budget": "number",
      "bhk": "number",
      "type": "rent|buy"
    }
  },
  {
    "name": "shortlist_property",
    "input": {
      "property_id": "string"
    }
  },
  {
    "name": "schedule_visit",
    "input": {
      "property_ids": ["string"],
      "preferred_time": "datetime"
    }
  },
  {
    "name": "assign_agent",
    "input": {
      "visit_id": "string"
    }
  }
]

4. 💬 WhatsApp Integration - Meta WhatsApp Cloud API

Flow:
User sends message
Webhook receives message
Pass to agent
Agent responds
Send reply back via WhatsApp API
5. 🔍 Property Search Layer
5.1 Data Sources
APIs/web search/Aggregated feeds/Internal DB (curated listings)
5.2 Search Pipeline
User Query → Normalize → Query Builder → Data Fetch → Ranking → Return Top N
5.3 Ranking Logic

Score properties based on:

Budget match (weight: 30%)
Location proximity (30%)
Amenities (20%)
User preferences history (20%)

6. 🗂️ Database Design
6.1 Tables
Users
user_id
phone_number
name
preferences (JSON)

Properties
property_id
title
location
price
bhk
type
source
owner_contact
metadata (JSON)

Shortlists
id
user_id
property_id
status
Visits
visit_id
user_id
property_ids
scheduled_time
status
assigned_agent_id

Agents (Field Staff)
agent_id
name
phone
availability
location

7. 📅 Scheduling System
7.1 Logic
Check owner availability (manual or API)
Batch multiple properties into one trip
Optimize by location clustering

8. 👨‍💼 Field Agent Assignment
Assignment Logic
Nearest available agent
Load balancing
Property cluster proximity
if agent.available and distance < threshold:
    assign(agent)

9. 🧠 LLM Stack
Recommended Setup
Layer	Tool
LLM	openRouter free models
Orchestration	any agent building sdk
Memory	Redis / Vector DB
RAG	Property embeddings
9.1 Memory Design
Short-term: conversation context
Long-term: user preferences

Example: User prefers: 2BHK, near school, budget < 25k

10. 🔄 Agent Workflow
Step-by-Step Flow
User: "Looking for 2BHK in Thane under 25k"
Agent:
Extract intent
Call search_properties
Show top 5 results
User shortlists 3
Agent:
Confirms shortlist
Calls schedule_visit
Agent assigns field staff
Sends confirmation
11. ⚙️ Backend Tech Stack

Recommended Stack
Backend: FastAPI (Python)
LLM Integration: LangChain / direct API
Database: sqllite
Cache: Redis
Queue: Celery / BullMQ
Hosting: AWS

12. 📊 Observability & Logging

Track:
Conversations
Tool calls
Drop-offs
Conversion rate (search → visit)

13. 🔐 Security & Compliance
Mask user phone numbers
Encrypt sensitive data
Respect platform TOS (critical for NoBroker)
Opt-in consent for WhatsApp


14. Deployment:
User render for hosting this application

Free compoenents:
| Component | Free Option                 |
| --------- | --------------------------- |
| Database  | Supabase (Postgres)         |
| Cache     | Upstash (Redis)             |
| Queue     | Cloudflare Workers / queues |
| Domain    | Freenom (free domains)      |
