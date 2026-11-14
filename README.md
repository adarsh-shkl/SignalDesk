# SignalDesk — Local NSE Trading Agent (V5)

**Read-only research dashboard** for the user's own NSE stock watchlist, with a Shoonya live-market feed when authorized, a native Python technical-indicator engine, a TradingView Lightweight Charts-based display, and a visual condition builder. No automatic orders, no SQLite, no cloud hosting, no paid TradingView plan.

## Quick start — Windows

1. Download and extract the ZIP into a folder (e.g. `C:\Projects\SignalDesk`). **Do not run the BAT directly from inside the ZIP.**
2. Install Python 3.11+ from python.org and check **Add Python to PATH**. An internet connection is needed on first run to install open-source Python packages and fetch Lightweight Charts from the CDN.
3. Double-click `start_windows.bat`. The script creates `.venv`, installs dependencies, and starts a localhost-only server.
4. Open **http://127.0.0.1:8000** in your browser (the BAT attempts to open it automatically).
5. Start in **DEMO / TEST** mode. Click **Run scanner** to request actual historical NSE stock candles, using Yahoo Finance for the 15-minute/hourly timeframes and NSE Bhavcopy MCP for daily/monthly. These are for educational testing only. If unavailable, use **Offline synthetic test** to exercise the interface with deterministic synthetic candles. Try changing the watchlist, adding/removing rules, switching AND/OR, toggling graph overlays, and clicking **Save strategy**. The saved watchlist/strategy is stored in `local_config.json` (gitignored).
6. Stop by closing the terminal window or pressing `Ctrl+C`.

**Test mode is not live data. It never makes stock purchase recommendations.**

## Shoonya live mode (only where authorized)

The app supports Shoonya's documented read-only OAuth + WebSocket flow. This has **NOT** been authenticated against a real Shoonya account in our test environment. The code should be treated as an integration candidate until a first live handshake is completed.

1. Shoonya account API access must be enabled and the account must be authorized to receive the NSE segment. You mentioned having a Client ID and API Key; Shoonya's **current OAuth flow additionally requires a Secret Code**, which may be labeled differently in your account. Verify the exact credentials on Shoonya's API Key Generation page. Do **not** share credentials in chat or commit them to GitHub.
2. Make a copy of `.env.example` named `.env`, then set:
   - `SHOONYA_CLIENT_ID=...`
   - `SHOONYA_SECRET_CODE=...`
   - optionally `SHOONYA_USER_ID=...` (OAuth usually returns it)
3. Shoonya's official IP whitelist guide currently says login/API calls require your source public IP to be whitelisted. **Do not circumvent this restriction.** If this computer is in Canada, use the historical research or offline synthetic mode for now; when you run in India, confirm Shoonya's permissions and outbound IP requirements. A changing home IP can break API login.
4. Restart the BAT, select **SHOONYA LIVE**, click **Open Shoonya sign-in**, log into the Shoonya-hosted page, and copy the one-time `code` from its registered redirect URL. Paste only that short-lived code into our app. Then click **Connect**.
5. Click **Start live watchlist feed**. The server resolves NSE instrument tokens, opens **one** WebSocket, and subscribes to the selected stocks.
6. During the trading session, confirm **CONNECTED** and a **fresh tick** for each symbol. Only then will **Run scanner** evaluate a symbol in live mode. Closed-market/stale symbols return `NOT_LIVE`; history-only results are not mislabeled as live.

Access tokens are held only in process memory when obtained through the UI and expire (Shoonya reports about 24 hours). If the token expires, sign in again. The app deliberately contains **no `place_order`, `modify_order` or `cancel_order` code paths**. It binds to `127.0.0.1`, so no one else on your network can access its UI.

### Important broker protocol note

Shoonya's documentation contains inconsistent REST examples: SearchScrip uses form `jData` and `jKey`, while historical candle pages show Bearer + JSON; the auth overview also mentions Bearer. The client handles both *documented read-only request shapes* for historical data, but a **live broker session** is required to verify which format your account currently accepts, available candle history, callback URL, and IP whitelist handling. There is no unverified claim that live Shoonya data currently works.

## Features in this package

- **Local dashboard:** English interface with initial Hindi labels, charts, stock list, match explanations, selected symbol/timeframe.
- **Watchlist:** 1–50 NSE equities in this pilot (Shoonya may allow up to 500 subscribed instruments on one WebSocket; large-scale historical scanning and REST limits need further optimization).
- **Add conditions:** Choose indicator/field, timeframe (15m / 1h / daily / monthly), numeric threshold or another indicator, comparator, enable/disable; combine with `AND`/`OR` and save to JSON.
- **Chart overlays:** Independent show/hide toggles for Bollinger Bands, SMA7/20, EMA50/200, VWAP, standard Supertrend approximation, supply/demand price levels, BOS/CHoCH/FVG markers. RSI14 is shown in a separate panel, with a threshold line.
- **Built-in calculations:** SMA7/20, EMA50/200, BB 20/2 (Pine's population standard deviation), ATR50, intraday VWAP, RSI14, simple confirmed swing structures, adapted supply/demand and FVG geometry.
- **Safety:** No automated BUY/SELL or order execution. All rule evaluations explicitly describe which timeframe passed/failed/needs data.

### Not implemented / parity gaps

The reference file `reference/original_indicators_as_received.pdf` is the uploaded 8-page printout of the original indicator. I also found the unwrapped source text in the user's Gmail thread, but this package **does not yet contain a full exact line-by-line port**. The exact original Supertrend Channel, Fluid Trade object-state behavior, LuxAlgo-style SMC internal/swing state machine, order blocks, equal highs/lows, higher-timeframe markers, pivots/CPR, Heikin Ashi smoothing, numerous graphical box states, and alert equivalence require separate verified work. RSI14 and monthly squeeze research rules were separately requested; they're not automatically claims about the source script.

TradingView Supercharts and Pine Script execution are **not** embedded. This project independently calculates supported indicator signals and renders them with the free [Lightweight Charts](https://tradingview.github.io/lightweight-charts/) library (TradingView attribution is in the dashboard). Calculations can differ from TradingView because of broker candles, smoothing/indicator implementation, missing history, candle boundaries, and unfinished bars. We must compare results candle-by-candle before treating signals as reliable.

## Project structure

```
app/
  server.py         FastAPI endpoints, config persistence, scan/chart routes
  shoonya.py        read-only OAuth, instrument lookup, history, WebSocket feed
  indicators.py     current native calculations and adapted structure logic
  research.py       credential-free historical data adapters, paging and cache
  engine.py         market/test candles, chart series, visual overlays
  rules.py          validated AND/OR strategy builder
  static/index.html single-page dashboard
reference/original_indicators_as_received.pdf
start_windows.bat
start_mac_linux.sh
.env.example
requirements.txt
tests/
```

To run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Sources

- [Shoonya OAuth login](https://shoonya.com/api-documentation/manual-login-oauth)
- [Shoonya WebSocket overview](https://shoonya.com/api-documentation/websocket-overview)
- [Shoonya subscribe to market feed](https://shoonya.com/api-documentation/subscribe-market-feed)
- [Shoonya historical candles](https://shoonya.com/api-documentation/time-price-series)
- [Shoonya IP whitelisting](https://shoonya.com/api-documentation/ip-whitelisting)
- [TradingView Lightweight Charts](https://www.tradingview.com/lightweight-charts/)

Original indicator: as uploaded by user; header claims Mozilla Public License 2.0 and identifies its author. Its licensing and attribution must be preserved if a derived indicator implementation is shared.

## V4 — Shoonya live integration update

This release modifies the existing V3 code (not a new dashboard). Start with the same `start_windows.bat`. Set `SHOONYA_CLIENT_ID` and `SHOONYA_SECRET_CODE` in `.env`, and restart. Confirm whether your API key is actually the OAuth Secret Code; do not assume a legacy API key is interchangeable. Alternatively, use an already authorized short-lived `SHOONYA_ACCESS_TOKEN` and the correct `SHOONYA_USER_ID` / `SHOONYA_ACCOUNT_ID` locally. Never share the real secrets in chat or Git.

In **SHOONYA LIVE**, sign in on Shoonya, copy the authorization code (or the full redirect URL containing `code`), click Connect, then Start live watchlist feed. Watchlist prices refresh every five seconds, the loaded chart every ten seconds, and optional Auto-scan runs every sixty seconds with no overlapping scans. Click any stock row to load its chart. Disconnect stops the feed. A live scan is withheld if fresh price updates are missing. The app uses the provided feed timestamp (`ft`) as a conservative freshness proxy, not a guaranteed exchange trade timestamp. A current price can be absent even with a healthy socket. Scan results reflect their evaluation time, not every tick.

Intraday historical volume uses `intv`, not cumulative `v`; the live partial candle tracks observed highs/lows, but history/stream overlap, missing ticks, current-bar volume (especially VWAP and monthly volume), session gaps, corporate actions and entire Pine parity are not validated. Current bars are provisional and must not be treated as confirmed trading signals. Only the 50-stock pilot is supported, not 500-stock scanning. Historical REST refreshes are throttled; a large scan can take more than the auto-scan interval. No trades are submitted. Shoonya login uses interactive authorization and a short-lived session, not a static API key by itself.

Tested using synthetic data, mocked HTTP responses and WebSocket messages. The real Shoonya OAuth response, historical endpoint behavior, account permissions and static-IP requirements must still be validated on an authorized India-side machine. Canada-side testing should use DEMO historical research or OFFLINE/mock mode unless Shoonya has explicitly authorized the location and IP. No actual API credentials are embedded. The application runs locally; publishing the source to GitHub does not deploy the dashboard.

## V5 — DEMO / TEST uses actual historical stock data

- **DEMO / TEST:** for 15m/1h candles the private local research app queries Yahoo Finance using `yfinance` (`<NSE_SYMBOL>.NS`, up to 55 calendar days requested, `auto_adjust=False`; Yahoo may still adjust for splits). Daily and monthly candles query the official NSE Bhavcopy MCP endpoint using its `get_stock_history` tool. Daily requests go back up to two years; monthly candles aggregate up to five years of NSE daily OHLCV using first open, max high, min low, last close and summed volume. Each NSE request covers up to three months, using `next_end_date` to page backwards. Latest intraday/monthly bars and the first monthly bar can be incomplete; corporate actions or feed differences can change comparison with TradingView. Neither provider is treated as a live feed.
- **OFFLINE / SYNTHETIC:** click the separate Offline synthetic test button to use the previous reproducible fixtures. No provider query or Shoonya credentials needed for scanner data; chart library still loads from CDN.
- **SHOONYA LIVE:** unchanged broker adapter, API credentials and authorization requirements. No orders.
- Sources and last evaluated candle timestamps appear per timeframe in table rows and the chart header. Matches are historical research only. Missing provider data returns `UNAVAILABLE` or `DEMO_INCOMPLETE`, never synthetic fallback. A scan may take time; start with 2–5 stocks. The memory cache lasts 5 minutes for intraday, 15 minutes for NSE history. The 50-symbol pilot limit remains.
- **No paid market data needed for this educational demo.** NSE MCP is strictly informational/educational and not intended for real-time trading, investment advice or commercial deployment. Yahoo Finance/yfinance data is for personal research subject to Yahoo terms. Data availability/rate limits/splits can differ from Shoonya and TradingView. No unattended live scanner or buy/sell recommendations use the research feed.
- **Validation:** automated local tests exercise mocked NSE pagination/transport parsing, Yahoo symbol/interval selection, OHLCV normalization and aggregation, source metadata and error paths. Outbound access to the NSE endpoint is unavailable in the build environment, and yfinance is not installed here; real external requests and browser rendering require verification on the user's computer. No broker credentials, purchased service or deployment.
- **Windows startup:** checks pip and attempts `ensurepip` repair; if still broken it recreates only the project's `.venv` directory. Using the newly extracted folder is recommended. Copy your old `local_config.json` if you wish to preserve rules; copy `.env` locally only if you need Shoonya mode. Do not copy `.venv`. Stop V4 before launching V5 to avoid a port conflict.

References:
- NSE MCP: https://www.nseindia.in/nse-mcp (official endpoints, no authentication, data-use restrictions, 3-month pages).
- NSE tool shape reference: https://github.com/imagine154/nse-mcp-explorer/blob/main/docs/NSE_MCP_CAPABILITIES.md (third-party observed schema; live schema remains to be validated).
- yfinance: https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html (intervals and intraday history limit).
