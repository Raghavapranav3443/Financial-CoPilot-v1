# **Financial CoPilot v1 (No Google Oauth, local-only + demo mode)** 🚀  
> **AI-powered Financial Workflow Tool** for quick invoice-payment reconciliation with a simple dashboard.


## ✨ What It Does
Financial CoPilot automates the tedious process of matching invoices and payments. It works entirely **offline** or with your **local CSV files**, and provides a clean **web dashboard** for results.

### 🔹 Core Features
- ✅ **Demo Mode**: Instantly generate synthetic invoices & payments for testing.
- ✅ **Local CSV Mode**: Load your own invoice/payment files and reconcile them.
- ✅ **Smart Matching**: Detect matches, mismatches, duplicates, and missing payments.
- ✅ **Reports**: Export results as **JSON** and **CSV**.
- ✅ **Dashboard**: Simple Flask-based UI to view and download reports.

## ⚡ Quick Start

### 1️⃣ Setup Environment
```bash
python -m venv venv
source venv/bin/activate   # On Windows: .\venv\Scripts\activate
pip install -r requirements.txt
```

### 2️⃣ Run Demo Mode (no files needed)
```bash
python main.py --demo --serve
```
- Generates mock invoices & payments.
- Opens dashboard at 👉 **http://127.0.0.1:5000**

### 3️⃣ Use Your Own CSV Files
Place your files in a folder, e.g. `./data`:
```
data/
  invoices.csv
  payments.csv
```
Run:
```bash
python main.py --local-dir ./data --serve
```

## 🧩 CSV Format
**Invoices CSV** (flexible):
```
invoice_id,vendor,date,amount,currency,description
```
Accepts variations like `invoice_date`, `supplier`, `id`.

**Payments CSV**:
```
payment_id,vendor,date,amount,currency,method,reference
```
Accepts `payment_date`, `settled_date`, `beneficiary`, etc.



## 🧰 Tech Stack
| Layer          | Tools / Libraries |
|---------------|--------------------|
| **Data**      | pandas, pydantic  |
| **UI**        | Flask             |
| **Language**  | Python 3.9+       |



## ⚙️ Requirements
```
pandas
flask
pydantic
```

## 🔍 CLI Options
- `--demo` : Generate mock data.
- `--local-dir <path>` : Load CSV files from folder.
- `--serve` : Launch dashboard.
- `--amount-tolerance <float>` : Allowed ₹ difference for match (default: 1.0).
- `--date-window-days <int>` : Days around invoice date to search payments (default: 7).

Example:
```bash
python main.py --local-dir ./data --amount-tolerance 5 --date-window-days 10 --serve
```


## 📂 Output
- `report.json` : Full structured report.
- `report.csv` : Spreadsheet-friendly version.
