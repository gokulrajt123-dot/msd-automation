# MSD Automation

Logs in to MSD (Dynamics 365) and downloads the reports automatically into the `downloads` folder.

## One-time setup (new computer)

1. **Install Python** from https://www.python.org/downloads/
   - On the first installer screen, tick **"Add python.exe to PATH"**, then click **Install Now**.
2. **Download this tool**: on this GitHub page click the green **Code** button → **Download ZIP**.
3. Right-click the downloaded ZIP → **Extract All…** → choose a folder (e.g. `Documents\MSD Automation`).
4. Open that folder and double-click **`Run MSD Automation.bat`**.
   - The first time it installs what it needs (takes a few minutes, needs internet).
   - Notepad opens: replace `your-msd-id` and `your-password-here` with your MSD login, then **File → Save** and close Notepad.

## Daily use

- Double-click **`Run MSD Automation.bat`**.
- Reports are saved in `downloads\<today's date>\`.
- **`Test - Today Only.bat`** downloads only today's data (quick check).
- **`Run Sales Register.bat`** downloads the Sales register on its own (`Test - Sales Register Today.bat` = today only).

## If something goes wrong

- **"Python is not installed"** → do step 1 again and make sure "Add python.exe to PATH" is ticked.
- **"Invalid username or password"** → open the file `.env` in the folder with Notepad and fix the login.
- Screenshots of errors are saved in the `logs` folder.

Your password stays only in `.env` on your own computer — it is never uploaded.
