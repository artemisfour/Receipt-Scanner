import base64
import csv
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

from flask import Flask, flash, redirect, render_template, request, send_file, session, url_for
from openai import OpenAI

DATA_PATH = Path(__file__).parent / "data"
STATE_FILE = DATA_PATH / "receipts.json"


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key")
    DATA_PATH.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        STATE_FILE.write_text(json.dumps({"accounts": {}}), encoding="utf-8")

    @dataclass
    class Receipt:
        date: str
        vendor: str
        total: float
        scan_id: str
        filename: str

    def load_state() -> Dict:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))

    def save_state(state: Dict) -> None:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def get_accounts() -> Dict[str, Dict]:
        state = load_state()
        return state.get("accounts", {})

    def set_accounts(accounts: Dict[str, Dict]) -> None:
        save_state({"accounts": accounts})

    def ensure_account(name: str) -> None:
        accounts = get_accounts()
        if name not in accounts:
            accounts[name] = {"receipts": [], "total": 0.0}
            set_accounts(accounts)

    def decode_image(file_storage) -> str:
        content = file_storage.read()
        b64 = base64.b64encode(content).decode("utf-8")
        mime = file_storage.mimetype or "image/jpeg"
        return f"data:{mime};base64,{b64}"

    def build_prompt(scan_id: str) -> str:
        return (
            "You are an assistant that extracts structured receipt data. "
            "Return a JSON object with keys date, vendor, total, scan_id. "
            "Use the provided scan_id and parse the receipt date, vendor name, and total amount. "
            "Always output a single JSON object only."
        ) + (f" Provided scan id: {scan_id}." if scan_id else "")

    def extract_receipt(client: OpenAI, image_url: str, scan_id: str) -> Optional[Receipt]:
        messages = [
            {"role": "system", "content": "You extract receipt details."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": build_prompt(scan_id)},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ]

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=200,
        )
        content = response.choices[0].message.content
        if not content:
            return None
        try:
            parsed = json.loads(content)
            return Receipt(
                date=str(parsed.get("date", "")),
                vendor=str(parsed.get("vendor", "")),
                total=float(parsed.get("total", 0)),
                scan_id=str(parsed.get("scan_id", scan_id)),
                filename="",
            )
        except (json.JSONDecodeError, ValueError):
            return None

    @app.route("/", methods=["GET"])
    def index():
        accounts = get_accounts()
        selected = request.args.get("account") or next(iter(accounts), "default")
        if selected and selected not in accounts:
            ensure_account(selected)
            accounts = get_accounts()
        receipts = accounts.get(selected, {}).get("receipts", []) if selected else []
        total = accounts.get(selected, {}).get("total", 0) if selected else 0
        return render_template(
            "index.html",
            accounts=accounts,
            selected_account=selected,
            receipts=receipts,
            total=total,
            api_key=session.get("api_key"),
        )

    @app.route("/api-key", methods=["POST"])
    def set_api_key():
        key = request.form.get("api_key", "").strip()
        if not key:
            flash("API key is required to process receipts.", "error")
            return redirect(url_for("index"))
        session["api_key"] = key
        flash("API key saved for this session.", "success")
        return redirect(url_for("index"))

    @app.route("/accounts", methods=["POST"])
    def create_account():
        name = request.form.get("account_name", "").strip()
        if not name:
            flash("Account name is required.", "error")
            return redirect(url_for("index"))
        ensure_account(name)
        flash(f"Account '{name}' ready.", "success")
        return redirect(url_for("index", account=name))

    @app.route("/upload", methods=["POST"])
    def upload_receipts():
        if "api_key" not in session:
            flash("Set your OpenAI API key first.", "error")
            return redirect(url_for("index"))
        account = request.form.get("account", "").strip()
        ensure_account(account or "default")
        accounts = get_accounts()
        scan_ids_raw = request.form.get("scan_ids", "").strip()
        scan_ids = [s.strip() for s in scan_ids_raw.splitlines() if s.strip()]
        files = request.files.getlist("receipts")
        if not files:
            flash("Upload at least one receipt image.", "error")
            return redirect(url_for("index", account=account))
        if scan_ids and len(scan_ids) not in (1, len(files)):
            flash("Provide either one Scan ID for all files or one per file.", "error")
            return redirect(url_for("index", account=account))

        client = OpenAI(api_key=session["api_key"])
        receipts: List[Receipt] = []
        for idx, file_storage in enumerate(files):
            file_scan_id = scan_ids[0] if len(scan_ids) == 1 else scan_ids[idx] if scan_ids else ""
            image_url = decode_image(file_storage)
            receipt = extract_receipt(client, image_url, file_scan_id)
            if receipt:
                receipt.filename = file_storage.filename
                receipts.append(receipt)
            else:
                flash(f"Could not parse receipt for file {file_storage.filename}.", "error")

        if not receipts:
            return redirect(url_for("index", account=account))

        account_key = account or "default"
        ensure_account(account_key)
        accounts = get_accounts()
        account_state = accounts[account_key]
        for receipt in receipts:
            account_state["receipts"].append(asdict(receipt))
            account_state["total"] = float(account_state.get("total", 0.0)) + float(receipt.total)
        accounts[account_key] = account_state
        set_accounts(accounts)

        flash(f"Processed {len(receipts)} receipt(s) for account '{account_key}'.", "success")
        return redirect(url_for("index", account=account_key))

    @app.route("/export", methods=["GET"])
    def export_csv():
        account = request.args.get("account", "default")
        accounts = get_accounts()
        if account not in accounts:
            flash("Account not found.", "error")
            return redirect(url_for("index"))
        receipts = accounts[account]["receipts"]
        csv_path = DATA_PATH / f"{account}_receipts.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["Date", "Vendor", "Total", "Scan ID", "Filename"])
            writer.writeheader()
            for entry in receipts:
                writer.writerow(
                    {
                        "Date": entry.get("date", ""),
                        "Vendor": entry.get("vendor", ""),
                        "Total": entry.get("total", ""),
                        "Scan ID": entry.get("scan_id", ""),
                        "Filename": entry.get("filename", ""),
                    }
                )
        return send_file(csv_path, mimetype="text/csv", as_attachment=True)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
