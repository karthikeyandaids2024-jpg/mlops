# FinGuard Live — full-stack transaction screening

## Run on Windows (quickest)
1. Extract the ZIP using **Extract All**. Open the extracted **FinGuard-Live** folder.
2. Stop your old Python server with **Ctrl+C**.
3. Double-click **START_WINDOWS.bat**.
4. Open **http://127.0.0.1:8000** in Chrome or Edge.
5. Click **Create account**, then **Add customer**, then submit a transaction.

Python 3.10 or newer is required. Python is already available on the laptop used in the previous setup. **No pip install is needed to run the app.** Keep the terminal window open.

## Run inside VS Code
Open the folder containing `server.py`. Select Terminal > New Terminal and run:

```powershell
python server.py
```
If `python` is unavailable, use `py -3 server.py`.

If port 8000 is busy:

```powershell
python server.py --port 8001
```
Open http://127.0.0.1:8001 instead.

**Do not run `python -m http.server` for this version.** It needs its actual API backend. Do not double-click index.html or use Live Server: those methods do not start the database and authentication APIs.

## Update an existing installation safely
1. Stop the running backend with Ctrl+C.
2. Extract this ZIP into a temporary folder.
3. Copy its FinGuard-Live contents into your existing FinGuard-Live folder and replace matching code files.
4. **Keep your existing `data` folder. Do not delete it.** The ZIP contains no database and will not replace your saved accounts.
5. Run `python server.py` or START_WINDOWS.bat again, then press Ctrl+F5 in the browser.

The backend creates the new loan-assessments table automatically while preserving existing accounts, customers and transactions.

## Loan eligibility and repayments
Open **Loan eligibility**, select a saved customer, then enter loan amount, annual rate, tenure in months, monthly income, existing EMIs, essential expenses, credit score and missed payments. Borrower details are prefilled when the customer has an existing credit profile; all loan inputs remain editable. Loan assessments are stored separately and do not alter the saved customer profile.

Outputs: project eligibility, monthly EMI, total interest, total repayment, monthly surplus, repayment-capacity loan amount, ML credit-default score, per-rule checks, full amortization schedule and saved history. JSON and repayment CSV exports are available.

**Illustrative policy, not actual underwriting:** total EMIs <=40% of income; EMI fits the remaining budget after expenses and existing payments; credit score >=650; missed payments <=1; ML score <50% when the case is within the model range. The result is “not eligible” if any applicable check fails, “eligible” if all checks pass and the model is supported, or “manual review” if rules pass but ML is outside its training range. An incoming credit transaction is different from borrower credit risk.

**Math:** monthly rate r = annual percentage / 1200. EMI = P*r / (1-(1+r)^(-n)); at 0% interest EMI=P/n. Available EMI capacity = max(0, min(0.40*income-existing_EMIs, income-expenses-existing_EMIs)). The estimated affordable principal divides this capacity by the same EMI factor. Amounts are estimates assuming a fixed rate, no fees and a monthly reducing balance. The repayment-capacity amount is not a sanctioned limit and does not override failed credit checks.

The ML loan-to-annual-income range is 0.3–10 and the debt-to-income range is 0–75%. Outside these ranges the system withholds the ML score instead of pretending it has validated predictions. All model training remains synthetic.

## Interface
- **New transaction** is the home screen. No overview dashboard, model laboratory or project guide appears in the app.
- Signup/login with password hashing and server-side sessions.
- Add customer aliases, recorded balances and optional borrower profiles.
- Manually enter debit or credit transactions, channel, reference, local date/time, distance, other recent transactions and device status.
- Instant backend risk analysis and persistent assessment history.
- Inspect saved results; search/filter history; download JSON and CSV.

## What the risk labels mean
1. **Fraud:** trained Random Forest classifier, on both incoming credit and outgoing debit transactions. Five features: amount, hour, distance, transactions in the past hour, new device. High = model score >= 50%.
2. **Credit-default risk:** separate trained Random Forest based on the customer's optional income, debt ratio, credit score, missed payments and loan-to-income ratio. It evaluates the borrower profile, NOT whether a deposit is safe. If details are not provided, this result is not available.
3. **Debit balance check:** explicitly a deterministic balance rule, not a claimed ML model. High when the outgoing amount exceeds the recorded balance or uses >=80% of it. Incoming credit transactions show this as not applicable.
4. **Overall risk:** high if any available check is high. It is a review flag, not a banking approval/rejection.
5. **Logistic regression:** the two trained baseline models provide comparison scores in assessment details. They do not override the forest result.
6. **K-means:** clusters a customer using standardized balance, usual monthly transactions and product count. It provides descriptive segmentation, not a fraud verdict.

All existing model families are used server-side. Channel, customer name, reference and credit/debit direction are stored as context; they are not features of the trained fraud model. A different device/location affects model inputs only when you enter those signals manually; the app does not collect actual device fingerprints or GPS.

## Real-time meaning and balances
Each submitted entry is checked immediately by the backend, then saved. This is **live manual-entry screening**, not an automatic bank feed. The system does not send payments or connect to UPI/bank APIs. Submitted entries are assessment records, not confirmed settlements.

Customer balances are snapshots. The app shows the projected balance if the transaction were applied, but does **not** silently change a customer's saved balance. Hourly transaction count includes previously saved entries for that customer in the selected hour plus the current entry and the manually entered number of other transactions outside the app. It is capped at 12 only for model inference; the actual count is saved.

## Backend and storage
- Python standard-library HTTP server serving the UI and same-origin JSON API.
- SQLite database automatically created at `data/finguard.db` on first launch.
- PBKDF2-SHA256 passwords with 310,000 iterations and independent salts.
- Random 12-hour server-side sessions, HttpOnly SameSite cookies, CSRF tokens, same-origin/Host checks, login throttling, input validation and parameterized SQL.
- Per-account customer and transaction isolation; duplicate-submit idempotency keys.
- The server listens only on 127.0.0.1. It is intended for local demonstration, not public internet hosting.
- The database contains customer names and financial profile data without application-level encryption. Use fictional customer aliases for the college demo. No passwords or test accounts are bundled.

**Persistence:** signing out or restarting does not delete data. Keep the `data` folder to retain accounts/history. Back up `data/finguard.db` only after stopping the server. Anyone with local disk access may access the database. There is no password-reset/email service in this local build.

## Architecture
Browser HTML/CSS/JavaScript → authenticated Python JSON API → trained model inference + SQLite → saved assessment returned to browser.

## Endpoints
- POST /api/signup, /api/login, /api/logout
- GET /api/me
- GET/POST /api/customers
- GET/POST /api/transactions
- GET/POST /api/loans

Mutating authenticated endpoints require the session cookie and X-CSRF-Token from signup/login or /api/me.

## Retraining (optional)
The pre-trained models are included in `models/models.json`; you do not need to retrain for tomorrow's demonstration.

```powershell
python -m pip install -r requirements.txt
python ml/train.py
```
Restart the backend after retraining. Training exports Random Forest trees, standardized logistic-regression coefficients, K-means centroids and measured evaluation metrics. Fraud and loan datasets are generated with seed 42: 6,000 rows each, stratified 4,500/1,500 train/test split. The Random Forest has 45 trees, depth 6, minimum leaf size 18 and balanced class weights.

## Limitations
Models were trained on synthetic data and have not been validated on real banking transactions. Scores are uncalibrated and thresholds are illustrative. Low risk does not mean guaranteed legitimate. Feature sensitivity replaces one input with its training median; it is not causal attribution or SHAP. High credit risk is a borrower-level flag. Security measures are suitable for this local prototype, not a claim of production banking compliance. No model laboratory/report pages are exposed in the UI.

## Verification
Run `python test_backend.py` for isolated temporary-database tests. Checks cover trained-model parity (20 examples), signup/login/logout, CSRF and origin rejection, account isolation, customer validation, duplicate requests, debit and credit behavior, recorded history persistence and blocked database/source-file URLs. Loan tests additionally verify EMI (including 0% interest), amortization, eligible/not-eligible/review cases, idempotency, saved assessments and account isolation. JavaScript syntax was checked. Automated browser testing could not run because the browser executable is unavailable in the build environment.

## Troubleshooting
- **File not found:** open the folder that contains `server.py`, not the outer ZIP directory.
- **Old interface appears:** stop the old server, run `python server.py`, then use Ctrl+F5.
- **Cannot connect:** keep the backend terminal running; check the port printed there.
- **Address invalid:** use http://127.0.0.1:8000, not http://[::]:8000.
- **Model file missing:** extract the entire ZIP, including `models`.
- **Signup email already used:** sign in with that local account.
