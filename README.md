# Receipt Scanner

A simple Flask app that uploads receipt photos, uses OpenAI's ChatGPT vision to extract details, and builds per-account CSVs with a running total.

## Features
- Enter your OpenAI API key to enable ChatGPT processing.
- Create and switch between multiple accounts.
- Upload one or more receipt photos and optionally provide Scan IDs (single value for all files or one per line matching file order).
- Extracted fields: **Date, Vendor, Total, Scan ID** plus the original filename.
- Running total is maintained for each account.
- Export each account's spreadsheet as a CSV.

## Setup
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Run the app:
   ```bash
   python app/app.py
   ```
3. Open `http://localhost:5000` and provide your OpenAI API key when prompted.

> The app stores account data locally under `app/data/receipts.json` and exports CSVs to the same folder.
