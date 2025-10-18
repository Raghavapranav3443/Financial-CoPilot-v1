"""
Financial CoPilot . v1 — Local-only version (no Google OAuth)

Features:
1) Demo mode: generate synthetic invoices & payments, then reconcile and serve a UI.
2) Local CSV mode: read CSVs from a folder, reconcile, then serve a UI.

Usage examples:
  python main.py --demo --serve
  python main.py --local-dir ./data --serve

"""

from __future__ import annotations
import os
import json
import argparse
import io
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
import pathlib
import tempfile
import pandas as pd
from pydantic import BaseModel, validator
from flask import Flask, jsonify, send_file, render_template_string

# --------------------------
# Configuration
# --------------------------
REPORT_JSON = "report.json"
REPORT_CSV = "report.csv"

# --------------------------
# Data models (Invoice/Payment)
# --------------------------
class Invoice(BaseModel):
    invoice_id: str
    vendor: str
    date: datetime
    amount: float
    currency: str = "INR"
    description: Optional[str] = ""

    @validator("date", pre=True)
    def parse_date(cls, v):
        if isinstance(v, datetime):
            return v
        if v is None or v == "":
            raise ValueError("Missing date")
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(str(v), fmt)
            except Exception:
                continue
        raise ValueError(f"Invalid date format: {v}")

class Payment(BaseModel):
    payment_id: str
    vendor: str
    date: datetime
    amount: float
    currency: str = "INR"
    method: Optional[str] = ""
    reference: Optional[str] = ""

    @validator("date", pre=True)
    def parse_date(cls, v):
        if isinstance(v, datetime):
            return v
        if v is None or v == "":
            raise ValueError("Missing date")
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(str(v), fmt)
            except Exception:
                continue
        raise ValueError(f"Invalid date format: {v}")

# --------------------------
# Reconciliation logic
# --------------------------
def reconcile(
    invoices: List[Invoice],
    payments: List[Payment],
    amount_tolerance: float = 1.0,
    date_window_days: int = 7
) -> Tuple[List[dict], dict]:
    results = []
    used_payments = set()
    payments_by_vendor: Dict[str, List[Payment]] = {}
    for p in payments:
        payments_by_vendor.setdefault(p.vendor.strip().lower(), []).append(p)

    for inv in invoices:
        vendor_key = inv.vendor.strip().lower()
        candidates = payments_by_vendor.get(vendor_key, [])
        window_start = inv.date - timedelta(days=date_window_days)
        window_end = inv.date + timedelta(days=date_window_days)

        close_amt = []
        amt_mismatch = []
        for p in candidates:
            if p.payment_id in used_payments:
                continue
            if not (window_start <= p.date <= window_end):
                continue
            if abs(p.amount - inv.amount) <= amount_tolerance:
                close_amt.append(p)
            else:
                amt_mismatch.append(p)

        if len(close_amt) == 1:
            p = close_amt[0]
            used_payments.add(p.payment_id)
            notes = "Late payment" if p.date > inv.date + timedelta(days=30) else None
            results.append({
                "invoice_id": inv.invoice_id,
                "matched_payment_id": p.payment_id,
                "invoice_amount": inv.amount,
                "payment_amount": p.amount,
                "vendor": inv.vendor,
                "invoice_date": inv.date.isoformat(),
                "payment_date": p.date.isoformat(),
                "status": "MATCHED",
                "notes": notes
            })
        elif len(close_amt) > 1:
            close_amt.sort(key=lambda x: abs((x.date - inv.date).days))
            p = close_amt[0]
            used_payments.add(p.payment_id)
            results.append({
                "invoice_id": inv.invoice_id,
                "matched_payment_id": p.payment_id,
                "invoice_amount": inv.amount,
                "payment_amount": p.amount,
                "vendor": inv.vendor,
                "invoice_date": inv.date.isoformat(),
                "payment_date": p.date.isoformat(),
                "status": "MATCHED",
                "notes": f"Multiple matches, picked closest by date ({len(close_amt)})"
            })
        elif amt_mismatch:
            amt_mismatch.sort(key=lambda x: abs((x.date - inv.date).days))
            p = amt_mismatch[0]
            used_payments.add(p.payment_id)
            results.append({
                "invoice_id": inv.invoice_id,
                "matched_payment_id": p.payment_id,
                "invoice_amount": inv.amount,
                "payment_amount": p.amount,
                "vendor": inv.vendor,
                "invoice_date": inv.date.isoformat(),
                "payment_date": p.date.isoformat(),
                "status": "AMOUNT_MISMATCH",
                "notes": f"Amount differs by {abs(p.amount - inv.amount):.2f}"
            })
        else:
            results.append({
                "invoice_id": inv.invoice_id,
                "matched_payment_id": None,
                "invoice_amount": inv.amount,
                "payment_amount": None,
                "vendor": inv.vendor,
                "invoice_date": inv.date.isoformat(),
                "payment_date": None,
                "status": "UNMATCHED",
                "notes": "No payment found in vendor+date window"
            })

    # duplicates and unused payments
    unused_payments = [p for p in payments if p.payment_id not in used_payments]
    seen: Dict[tuple, List[Payment]] = {}
    duplicate_flags = []
    for p in payments:
        key = (p.vendor.strip().lower(), round(p.amount, 2), p.date.date())
        seen.setdefault(key, []).append(p)
    for key, items in seen.items():
        if len(items) > 1:
            duplicate_flags.append({
                "vendor": key[0],
                "date": str(key[2]),
                "amount": float(key[1]),
                "count": len(items),
                "payment_ids": [x.payment_id for x in items]
            })

    summary = {
        "source": "local-csv-or-demo",
        "total_invoices": len(invoices),
        "total_payments": len(payments),
        "matched": sum(1 for r in results if r["matched_payment_id"]),
        "unmatched_invoices": sum(1 for r in results if r["status"] == "UNMATCHED"),
        "amount_mismatches": sum(1 for r in results if r["status"] == "AMOUNT_MISMATCH"),
        "duplicate_payment_flags": duplicate_flags,
        "unused_payments_count": len(unused_payments)
    }
    return results, summary

# --------------------------
# CSV parsing utilities
# --------------------------
def parse_invoices_from_dataframe(df: pd.DataFrame) -> List[Invoice]:
    invoices = []
    for _, row in df.fillna("").iterrows():
        data = {
            "invoice_id": str(row.get("invoice_id") or row.get("id") or f"INV-{uuid.uuid4().hex[:8]}"),
            "vendor": str(row.get("vendor") or row.get("payee") or row.get("supplier") or "UNKNOWN"),
            "date": row.get("date") or row.get("invoice_date") or row.get("issued_date"),
            "amount": float(row.get("amount") or 0),
            "currency": row.get("currency") or "INR",
            "description": row.get("description") or ""
        }
        try:
            invoices.append(Invoice(**data))
        except Exception as e:
            print("Skipping invoice row due to parse error:", e, data)
    return invoices

def parse_payments_from_dataframe(df: pd.DataFrame) -> List[Payment]:
    payments = []
    for _, row in df.fillna("").iterrows():
        data = {
            "payment_id": str(row.get("payment_id") or row.get("id") or f"PAY-{uuid.uuid4().hex[:8]}"),
            "vendor": str(row.get("vendor") or row.get("payee") or row.get("beneficiary") or "UNKNOWN"),
            "date": row.get("date") or row.get("payment_date") or row.get("settled_date"),
            "amount": float(row.get("amount") or 0),
            "currency": row.get("currency") or "INR",
            "method": row.get("method") or "",
            "reference": row.get("reference") or ""
        }
        try:
            payments.append(Payment(**data))
        except Exception as e:
            print("Skipping payment row due to parse error:", e, data)
    return payments

def load_local_csvs(folder: str) -> Tuple[List[Invoice], List[Payment]]:
    invoices: List[Invoice] = []
    payments: List[Payment] = []
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"Local folder not found: {folder}")

    for pth in pathlib.Path(folder).glob("*.csv"):
        try:
            df = pd.read_csv(pth)
        except Exception as e:
            print(f"Failed to read {pth}: {e}")
            continue
        cols = {c.lower() for c in df.columns}
        # heuristic to decide type
        if {"invoice_id", "invoice_date"}.intersection(cols) or "supplier" in cols or "invoice" in " ".join(cols):
            invoices += parse_invoices_from_dataframe(df)
            print(f"Parsed invoices from {pth.name}: {len(invoices)} total")
        elif {"payment_id", "payment_date", "beneficiary"}.intersection(cols) or "payment" in " ".join(cols):
            payments += parse_payments_from_dataframe(df)
            print(f"Parsed payments from {pth.name}: {len(payments)} total")
        else:
            # fallback: decide by numeric distribution (optional)
            # default to payments
            payments += parse_payments_from_dataframe(df)
            print(f"[Heuristic] Treated {pth.name} as payments: {len(payments)} total")
    return invoices, payments

# --------------------------
# Reporting
# --------------------------
def export_report(results: List[dict], summary: dict, json_path=REPORT_JSON, csv_path=REPORT_CSV):
    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "summary": summary,
        "results": results
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    df = pd.DataFrame(results)
    df.to_csv(csv_path, index=False)
    return json_path, csv_path

# --------------------------
# Flask UI
# --------------------------
def make_app(report_path=REPORT_JSON):
    app = Flask(__name__)

    @app.route("/")
    def index():
        if not os.path.exists(report_path):
            return "<h3>No report found. Run --demo or --local-dir first.</h3>"
        with open(report_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        s = data.get("summary", {})
        results = data.get("results", [])[:200]
        return render_template_string("""
        <h2>Financial Copilot — Reconciliation Report</h2>
        <p>Generated: {{gen}}</p>
        <ul>
          <li>Source: {{s.source}}</li>
          <li>Total invoices: {{s.total_invoices}}</li>
          <li>Total payments: {{s.total_payments}}</li>
          <li>Matched: {{s.matched}}</li>
          <li>Unmatched: {{s.unmatched_invoices}}</li>
          <li>Amount mismatches: {{s.amount_mismatches}}</li>
          <li>Unused payments: {{s.unused_payments_count}}</li>
        </ul>
        <table border="1" cellpadding="4">
          <tr><th>Invoice ID</th><th>Vendor</th><th>Invoice Date</th><th>Invoice Amt</th>
              <th>Payment ID</th><th>Payment Date</th><th>Payment Amt</th><th>Status</th><th>Notes</th></tr>
          {% for r in results %}
            <tr>
              <td>{{ r.invoice_id }}</td>
              <td>{{ r.vendor }}</td>
              <td>{{ r.invoice_date[:10] }}</td>
              <td>{{ "%.2f"|format(r.invoice_amount) }}</td>
              <td>{{ r.matched_payment_id or "" }}</td>
              <td>{{ r.payment_date[:10] if r.payment_date }}</td>
              <td>{{ "%.2f"|format(r.payment_amount) if r.payment_amount }}</td>
              <td>{{ r.status }}</td>
              <td>{{ r.notes or "" }}</td>
            </tr>
          {% endfor %}
        </table>
        <p>/downloadDownload JSON</a>  /csvDownload CSV</a></p>
        """, gen=data.get("generated_at"), s=s, results=results)

    @app.route("/download")
    def download():
        if not os.path.exists(report_path):
            return "No report", 404
        return send_file(report_path, as_attachment=True, download_name=os.path.basename(report_path))

    @app.route("/csv")
    def csvdl():
        if not os.path.exists(REPORT_CSV):
            return "CSV not found", 404
        return send_file(REPORT_CSV, as_attachment=True, download_name=os.path.basename(REPORT_CSV))

    @app.route("/api/report")
    def api_report():
        if not os.path.exists(report_path):
            return jsonify({"error": "no report"}), 404
        with open(report_path, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return app

# --------------------------
# Mock generator (demo)
# --------------------------
def generate_sample_data(n=12) -> Tuple[List[Invoice], List[Payment]]:
    import random
    vendors = [
        "ACME Corp", "BlueBank Services", "Quick Logistics", "Alpha Insure", "Zeta Telecom",
        "Nimbus Soft", "Orion Foods", "Delta Engineering", "Astra Labs", "Vertex Retail"
    ]
    base_date = datetime.today()
    invoices: List[Invoice] = []
    payments: List[Payment] = []
    for i in range(n):
        vendor = random.choice(vendors)
        inv_date = base_date - timedelta(days=random.randint(0, 60))
        amt = round(random.uniform(500, 20000), 2)
        invoices.append(Invoice(
            invoice_id=f"INV-{1000+i}",
            vendor=vendor,
            date=inv_date,
            amount=amt
        ))
        if random.random() < 0.75:
            paid_amt = amt
            if random.random() < 0.15:
                paid_amt = round(amt * random.uniform(0.85, 1.15), 2)
            pay_date = inv_date + timedelta(days=random.randint(0, 40))
            payments.append(Payment(
                payment_id=f"PAY-{2000+i}",
                vendor=vendor,
                date=pay_date,
                amount=paid_amt
            ))
    # add one duplicate if payments exist
    if payments:
        dup = payments[0]
        payments.append(Payment(
            payment_id=f"PAY-DUP-{uuid.uuid4().hex[:6]}",
            vendor=dup.vendor,
            date=dup.date,
            amount=dup.amount
        ))
    return invoices, payments

# --------------------------
# CLI / main logic
# --------------------------
def main():
    parser = argparse.ArgumentParser(description="Financial CoPilot (local-only)")
    parser.add_argument("--demo", action="store_true", help="Generate mock data and build report")
    parser.add_argument("--local-dir", default="", help="Folder containing CSVs (e.g., invoices.csv, payments.csv)")
    parser.add_argument("--serve", action="store_true", help="Start Flask UI to serve the last report")
    parser.add_argument("--amount-tolerance", type=float, default=1.0, help="₹ difference allowed to count as a match")
    parser.add_argument("--date-window-days", type=int, default=7, help="Days around invoice date to search payments")
    args = parser.parse_args()

    invoices: List[Invoice] = []
    payments: List[Payment] = []

    if args.demo:
        invoices, payments = generate_sample_data(20)
        print(f"Generated {len(invoices)} invoices and {len(payments)} payments (mock).")

    elif args.local_dir:
        invoices, payments = load_local_csvs(args.local_dir)
        print(f"Loaded {len(invoices)} invoices and {len(payments)} payments from local CSVs.")

    else:
        parser.print_help()
        return
    results, summary = reconcile(
        invoices, payments,
        amount_tolerance=args.amount_tolerance,
        date_window_days=args.date_window_days
    )
    export_report(results, summary)
    print("Report generated:", REPORT_JSON)

    if args.serve:
        app = make_app()
        app.run(debug=False)

if __name__ == "__main__":
    main()