# PLM Ticker Watcher

A full-stack stock valuation tool combining **DCF (Discounted Cash Flow)** and **Growth Model** analysis with AI-powered autofill, a live dashboard, and SQL Server persistence.

---

## Project Structure

```
ticker-valuation/
├── ticker_calculator.html   # Main valuation calculator (frontend)
├── dashboard.html           # Portfolio dashboard (frontend)
├── api.py                   # FastAPI backend (Python)
├── schema.sql               # SQL Server database schema
├── requirements.txt         # Python dependencies
├── .env                     # Environment variables (you create this)
└── .gitignore               # Git ignore file (you create this)
```

---

## Prerequisites

Install these before starting:

| Tool | Download | Notes |
|---|---|---|
| **VS Code** | https://code.visualstudio.com | Main editor |
| **Python 3.11+** | https://python.org/downloads | Check "Add to PATH" on Windows |
| **SQL Server** | Already installed locally | Any edition (Express is fine) |
| **SSMS** | https://aka.ms/ssmsfullsetup | SQL Server Management Studio |
| **ODBC Driver 17** | https://aka.ms/downloadmsodbcsql | Required for Python → SQL Server |
| **Node.js** | https://nodejs.org | Optional — only needed for Live Server via npm |

---

## VS Code Extensions

Open VS Code → Extensions (Ctrl+Shift+X) → install:

| Extension | Publisher | Purpose |
|---|---|---|
| **Python** | Microsoft | Python syntax, debugging |
| **SQLTools** | Matheus Teixeira | Run SQL inside VS Code |
| **SQLTools SQL Server** | Matheus Teixeira | SQL Server driver for SQLTools |
| **Live Server** | Ritwick Dey | Serve HTML with auto-reload |
| **Prettier** | Prettier | Auto-format HTML/JS |
| **REST Client** | Huachao Mao | Test API endpoints from VS Code |

---

## Step 1 — Open Project in VS Code

```bash
# Open terminal and navigate to your project folder
cd C:\Users\YourName\ticker-valuation

# Open VS Code in this folder
code .
```

---

## Step 2 — Create Virtual Environment

Open the VS Code terminal with `` Ctrl+` `` and run:

```bash
# Create virtual environment
python -m venv venv

# Activate it — Windows PowerShell
venv\Scripts\activate

# Activate it — Mac/Linux
source venv/bin/activate

# Install all dependencies
pip install -r requirements.txt
```

You should see `(venv)` in your terminal prompt.
VS Code will ask "Do you want to use this environment?" → click **Yes**.

---

## Step 3 — Create the Database

Open **SSMS**, connect to your local SQL Server instance, then run:

```sql
CREATE DATABASE dcf_db;
```

Then run the schema file. In SSMS:
1. File → Open → `schema.sql`
2. Change the database dropdown to `dcf_db`
3. Press **F5** to execute

You should see:
```
Commands completed successfully.
```

---

## Step 4 — Create .env File

In VS Code, create a new file named exactly `.env` in the project root:

```
# SQL Server connection
DB_SERVER=localhost
DB_NAME=dcf_db

# Windows Authentication (recommended for local dev — leave user/password blank)
DB_USER=
DB_PASSWORD=

# SQL Server login (use this if Windows Auth doesn't work)
# DB_USER=sa
# DB_PASSWORD=YourSQLPassword

# Anthropic API key — get from https://console.anthropic.com
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxx
```

> **Note:** If your SQL Server instance has a name (e.g. `DESKTOP-ABC\SQLEXPRESS`), use that as `DB_SERVER`.

---

## Step 5 — Get Anthropic API Key

1. Go to **https://console.anthropic.com**
2. Sign up / log in
3. Click **API Keys** → **Create Key**
4. Copy the key and paste into `.env` as `ANTHROPIC_API_KEY`
5. Go to **Billing** → add $5–10 credit (each AI Autofill costs ~$0.01)

---

## Step 6 — Create .gitignore

Create a `.gitignore` file so you never commit secrets:

```
venv/
.env
__pycache__/
*.pyc
*.pyo
.DS_Store
```

---

## Step 7 — Connect SQLTools to Database

1. Click the **SQLTools icon** in VS Code left sidebar (database cylinder icon)
2. Click **Add New Connection** → choose **Microsoft SQL Server**
3. Fill in:
   - **Connection Name:** `dcf_db`
   - **Server:** `localhost` (or your instance name)
   - **Database:** `dcf_db`
   - **Authentication:** `Windows Authentication` (or SQL Server with user/pass)
4. Click **Test Connection** → **Save Connection**

You can now run any SQL query directly in VS Code by right-clicking a `.sql` file → **Run on active connection**.

---

## Running the Project

### Every time you work on this project:

**Terminal 1 — Start the API:**
```bash
cd ticker-valuation
venv\Scripts\activate
uvicorn api:app --reload --port 8000
```

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

**Terminal 2 — Open the frontend:**

In VS Code, right-click `dashboard.html` → **Open with Live Server**

This opens at: `http://127.0.0.1:5500/dashboard.html`

To open the calculator directly:
Right-click `ticker_calculator.html` → **Open with Live Server**

> **Tip:** Split the VS Code terminal (click the split icon) so both API and Live Server run side by side.

---

## Verifying Everything Works

| Check | URL | Expected |
|---|---|---|
| API is running | http://localhost:8000 | `{"status": "DCF Valuation API is running..."}` |
| API health + DB | http://localhost:8000/api/health | `{"status": "ok", "database": "dcf_db"}` |
| Interactive API docs | http://localhost:8000/docs | Swagger UI with all endpoints |
| Dashboard | http://127.0.0.1:5500/dashboard.html | PLM Ticker Watcher dashboard |
| Calculator | http://127.0.0.1:5500/ticker_calculator.html | Valuation calculator |

---

## Using the Calculator

### Running a Valuation

1. Open `ticker_calculator.html` in Live Server
2. Type a ticker symbol (e.g. `TSLA`, `SAATVIKGL`)
3. Click **⚡ AI Autofill** — waits ~10–15 seconds while it:
   - Fetches the live stock price via web search
   - Populates all DCF + Growth model assumptions
   - Generates risks, rationale, and stress tests
4. Review and adjust any assumptions you disagree with
5. Click **▶ Recalculate DCF** (or **▶ Recalculate Growth Model**)
6. Switch between **★ Summary**, **📊 DCF**, **📈 Growth**, **🏦 Balance Sheet**, **⚠️ Risks**, **💡 Rationale** tabs
7. Click **⚙️ API Settings** → set API URL to `http://localhost:8000` → enter your name in **Created By**
8. Click **💾 Save Valuation** to persist to the database

### Valuation Modes

| Mode | Best For | Key Inputs |
|---|---|---|
| **📊 DCF** | Profitable or near-profit companies | Revenue growth, EBITDA margins, WACC |
| **📈 Growth** | Profitable companies with positive EPS | Base EPS, EPS growth rates, terminal P/E |

> ⚠️ **Growth Model requires positive EPS.** For pre-profit companies (e.g. IONQ, QBTS), use DCF mode or enter a forward analyst EPS estimate as Base EPS.

---

## Using the Dashboard

1. Open `dashboard.html` in Live Server
2. The dashboard auto-connects to `http://localhost:8000`
3. If not connected, enter the API URL and click **Connect**

### Features

| Feature | How |
|---|---|
| **Country tabs** | Switch between 🌍 All / 🇮🇳 India / 🇺🇸 US / 🌐 Other |
| **Filter** | All / Buy+Strong Buy / Hold / Sell |
| **Sort** | Ticker, Upside %, Date, Run count |
| **Expand ticker** | Click any ticker row to see all saved dates |
| **View run details** | Click **View** on any run → opens 6-tab detail modal |
| **Recalculate** | Click **↻ Recalc** → opens calculator with ticker pre-filled and AI Autofill auto-triggered |
| **Delete ticker** | Expand ticker → scroll to bottom → **🗑 Delete all runs** |
| **New valuation** | Click **＋ New Valuation** in header |

### Country Detection

The dashboard auto-detects the country from the exchange/currency saved during autofill:

| Market | Detected by |
|---|---|
| 🇮🇳 India | Exchange: NSE, BSE · Currency: INR, Crore |
| 🇺🇸 US | Exchange: NASDAQ, NYSE, AMEX · Currency: USD, USD mm |
| 🌐 Other | Anything else |

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | API status |
| `GET` | `/api/health` | Health check + DB connection test |
| `POST` | `/api/ai/messages` | Anthropic AI proxy (avoids browser CORS) |
| `POST` | `/api/valuations/save` | Save full valuation (input + calcs + comments) |
| `GET` | `/api/valuations` | All tickers with latest summary |
| `GET` | `/api/valuations/{ticker}` | Latest valuation for one ticker |
| `GET` | `/api/valuations/{ticker}/history` | All runs for a ticker |
| `GET` | `/api/valuations/{ticker}/runs` | All runs with DCF + Growth joined |
| `GET` | `/api/valuations/detail/{input_id}` | Full detail — inputs, calcs, comments |
| `DELETE` | `/api/valuations/ticker/{ticker}` | Delete all runs for a ticker |
| `DELETE` | `/api/valuations/{input_id}` | Delete one specific run |

Full interactive docs at: **http://localhost:8000/docs**

---

## Database Tables

| Table | Description |
|---|---|
| `dbo.TickerInput` | Every input field saved per run — market data, DCF assumptions, Growth assumptions, historical data |
| `dbo.TickerCalculation` | Computed outputs — intrinsic value, upside %, MOS prices, verdict (auto-computed) |
| `dbo.TickerKeyComments` | AI-generated rationale, risks, stress tests, balance sheet notes |
| `dbo.v_latest_valuations` | View — latest valuation per ticker with both models joined |

---

## Troubleshooting

**API not starting:**
```
Make sure venv is activated: venv\Scripts\activate
Check .env file exists and has no spaces around =
Run: python -c "import pyodbc; print(pyodbc.drivers())"
Should show "ODBC Driver 17 for SQL Server" in the list
```

**ODBC Driver not found:**
```
Download and install: https://aka.ms/downloadmsodbcsql
Choose: ODBC Driver 17 for SQL Server
Restart VS Code after installing
```

**Database connection error:**
```
Check DB_SERVER in .env matches your SQL Server instance name
Open SSMS → right-click server → Properties → copy the server name exactly
Try: DB_SERVER=localhost\SQLEXPRESS  (if using Express edition)
```

**AI Autofill not working:**
```
Check ANTHROPIC_API_KEY is set in .env with no spaces around =
Verify API is running: http://localhost:8000/api/health
Open browser console (F12) → Console tab for error details
Make sure you have credit at: https://console.anthropic.com/settings/billing
```

**CORS error in browser:**
```
All AI calls must go through the FastAPI proxy at localhost:8000
Never call api.anthropic.com directly from the browser
Make sure API Settings in calculator shows http://localhost:8000
```

**Live Server port conflict:**
```
Right-click HTML → Open with Live Server
If port 5500 is taken, Live Server uses 5501, 5502 etc.
Update the calculator path in dashboard API Settings bar accordingly
```

**Growth Model shows negative fair value:**
```
The company likely has negative EPS (pre-profit)
Solution 1: Switch to DCF mode
Solution 2: Enter a forward analyst EPS estimate as Base EPS
           (e.g. 2027 consensus estimate instead of current TTM EPS)
```

---

## Cost Estimate

| Action | API calls | Approx cost |
|---|---|---|
| AI Autofill (per click) | 2 calls ~3,500 tokens | ~$0.01 |
| $5 credit | — | ~500 autofills |
| $10 credit | — | ~1,000 autofills |

Manual input (without AI Autofill) is completely free — no API calls made.

---

## Daily Workflow

```
1. cd ticker-valuation
2. venv\Scripts\activate
3. uvicorn api:app --reload --port 8000    ← Terminal 1
4. Open dashboard.html with Live Server    ← Terminal 2 / VS Code
5. Work on valuations
6. Ctrl+C to stop uvicorn when done
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Vanilla HTML + CSS + JavaScript |
| Backend | Python 3.11 · FastAPI · uvicorn |
| Database | Microsoft SQL Server · pyodbc |
| AI | Anthropic Claude (claude-sonnet-4-5) via API |
| Dev Tools | VS Code · Live Server · SQLTools |

---

*PLM Ticker Watcher — built for personal investment research*