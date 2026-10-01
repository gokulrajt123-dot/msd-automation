# MSD Automation

Logs in to MSD (Dynamics 365) and downloads reports automatically into the `downloads` folder.
Just double-click a `.bat` file - no typing needed.

## One-time setup (new computer)

1. **Install Python** from https://www.python.org/downloads/
   - On the first installer screen, tick **"Add python.exe to PATH"**, then click **Install Now**.
2. **Download this tool**: on this GitHub page click the green **Code** button → **Download ZIP**.
3. Right-click the downloaded ZIP → **Extract All…** → choose a folder (e.g. `Documents\MSD Automation`).
4. Open that folder and double-click **`Run MSD Automation.bat`**.
   - The first time it installs what it needs (takes a few minutes, needs internet).
   - Notepad opens: fill in the two MSD logins (see below), then **File → Save** and close Notepad.

### The two MSD logins (`.env` file)

The `.env` file holds two logins:

```
EMAIL=...            main login - Microsoft email
USERNAME=...         main login - Royal Enfield SSO username
PASSWORD=...

PARTS_EMAIL=...      parts login - Microsoft email
PARTS_USERNAME=...   parts login - Royal Enfield SSO username
PARTS_PASSWORD=...
```

The **parts login** is used for Parts stock, REAssure Incentive Claims and Service claim report;
the **main login** for everything else. To change a login later, open `.env` with Notepad.

## Which file to double-click

| Double-click | Downloads | Period |
|---|---|---|
| **`Run MSD Automation.bat`** | Job Card Invoice Statement, Booking statement, Invoice statement (all 3 together) | 1st of this month → today |
| `Run Sales Register.bat` | Sales register | 1st of this month → today |
| `Run Purchase Register.bat` | Purchase register | 1st of this month → today |
| `Run Return Invoice Statement.bat` | Return Invoice Statement | 1st of this month → today |
| `Run Cancelled Booking Statement.bat` | Cancelled booking statement | 1st of this month → today |
| `Run Vehicle Stock Ageing.bat` | Vehicle Stock Ageing | stock as on today |
| `Run REAssure Incentive Claims.bat` | All claims (Part → Claims, parts login) - **very big, see below** | everything |
| `Run Service Claim Report.bat` | Service claim report (parts login) | 1st of this month → today |
| `Run Parts Stock.bat` | Parts stock (Part → Stock Report, parts login) | current stock |
| `Run GMA Stock.bat` | GMA stock (GMA → Stock Report) | current stock |
| `Run Gear Stock.bat` | Gear stock (Gear → Stock Report) | current stock |

- **On the 1st of a month**, "1st of this month → today" reports download the **whole previous month** instead.
- Files named **`Test - ... Today.bat`** download only today's data - use them for a quick check that everything works.

## Where the files go

`downloads\<today's date>\<report name>\` - for example
`downloads\2026-10-01\Sales register\Sales register 01-10-2026 to 01-10-2026.xlsx`.
Earlier files are never overwritten.

## While it runs

- Browser windows open by themselves - **don't close them**, and don't click inside them.
- Some reports take a long time (a full month can take 1-2 hours). The black window shows progress.
- If MSD asks for an approval or OTP on your phone, approve it - the tool waits for you.
- When it is done the black window says **SUCCESS** (or which report failed). Press Enter to close it.

## REAssure Incentive Claims - check the file

This downloads every claim (several years), so it can take a long time. At the end the black window shows
the number of rows and the oldest and newest claim date, for example:

```
'REAssure Incentive Claims': 83 rows, dates 14-Feb-2025 to 16-Sep-2026
>> PLEASE CHECK ...
```

Please check that the dates go back as far as expected and the number of rows looks right.
If it says **WARNING ... data is MISSING**, the file was cut off - tell the person who set this up.

## If something goes wrong

- **"Python is not installed"** → do setup step 1 again and make sure "Add python.exe to PATH" is ticked.
- **"Invalid username or password"** → open the file `.env` in the folder with Notepad and fix the login
  (`PARTS_...` lines for the parts reports).
- **A report failed** → just run its `.bat` again. Screenshots of errors are saved in the `logs` folder.
- **Getting an updated version** → download the ZIP again (setup steps 2-3) and copy your `.env` file from the old folder into the new one.
  If the new version needs a setting your `.env` doesn't have yet, compare it with `.env.example`.

Your password stays only in `.env` on your own computer - it is never uploaded.
