# MSD Automation

This tool opens MSD by itself and downloads your reports into a folder on your computer.
You don't need to click anything in MSD.

Everything is done from **one file**: **`Run MSD Automation.bat`**

---

## Part 1 - Setting it up (only once)

### Step 1: Install Python

1. Go to https://www.python.org/downloads/ and click the big yellow **Download Python** button.
2. Open the downloaded file.
3. **Important:** on the first screen, tick the box **"Add python.exe to PATH"** at the bottom.
4. Click **Install Now** and wait until it says it's finished. Close it.

### Step 2: Get the tool

1. On this GitHub page, click the green **Code** button, then **Download ZIP**.
2. Go to your **Downloads** folder. Right-click the ZIP file → **Extract All…** → **Extract**.
3. Move the extracted folder somewhere easy to find, e.g. **Documents**.

### Step 3: First start

1. Open the folder and double-click **`Run MSD Automation.bat`**.
   - If Windows shows a blue box "Windows protected your PC", click **More info** → **Run anyway**.
2. The first time, it gets itself ready. **This takes a few minutes** - just wait.
3. **Notepad opens.** Type your MSD logins after the `=` signs:

   ```
   EMAIL=        your MSD email, e.g. 1234-name@rebridge.co.in
   USERNAME=     your MSD username, e.g. 1234-name
   PASSWORD=     your MSD password

   PARTS_EMAIL=     the parts login email
   PARTS_USERNAME=  the parts login username
   PARTS_PASSWORD=  the parts login password
   ```

   **More than one branch?** Instead of the lines above, list the branches and give each its own
   logins, named after the branch (see `.env.example`):

   ```
   BRANCHES=Udumalpet, Pollachi
   UDUMALPET_EMAIL=...   UDUMALPET_PARTS_EMAIL=...   (and _USERNAME / _PASSWORD)
   POLLACHI_EMAIL=...    POLLACHI_PARTS_EMAIL=...    (and _USERNAME / _PASSWORD)
   ```

   Every report is then downloaded for each branch, into its own folder.

4. Click **File → Save**, then close Notepad.

### Step 4: Choose your automatic downloads

Right after that, the tool asks you three simple questions:

**Question 1 - How often should each report download by itself?**
It shows the reports one by one. For each, type a number and press **Enter**:

| Type | Means |
|---|---|
| **1** | **Daily** - every day |
| **2** | **Monthly** - once at the start of each month (the whole previous month) |
| **3** | **Some days** - only on the dates you type, e.g. **1,15** |
| **4** | **Not automatic** - only when you ask for it |
| *(just Enter)* | keep the choice shown in [ ] |

For example: Booking statement, Invoice statement, Service claim report and Job Card Invoice Statement
**Daily**; Sales register and Purchase register **Monthly**.

**Question 2 - In which order?**
It lists your automatic reports. Type their numbers in the order you want, e.g. **2,3,1,4**,
or just press **Enter** to keep the order shown.

**Question 3 - Start by itself?**
Type **Y**, then how many minutes after switching on the computer it should start (e.g. **10**).

That's it. From now on the reports download **by themselves** - nobody has to click anything.
You can change these answers any time with **S** in the menu.

---

## Part 2 - Using the menu

Double-click **`Run MSD Automation.bat`**. You will see a list of reports and these choices:

```
  S  Set up automatic downloads step by step (daily / monthly / order / start time)
  D  Download today's scheduled reports now
  C  Choose reports to download now
  O  Change the order
  H  Change how often some reports download
  A  Automatic download settings
  B  Browser windows: show / hide while downloading
  P  Send income to Paisa now
  X  Exit
```

**Type the letter and press Enter.**

### S - Set up automatic downloads
Asks the same three questions as the first time (how often, order, start time).
The easiest way to change anything.

### D - Download today's reports now
Downloads all reports that are due today, one after another.
Reports already downloaded today are skipped.

### C - Download some reports now
1. Type the numbers of the reports you want, with commas, e.g. **1,4,5** (or **A** for all). Press Enter.
2. Choose the dates:
   - **1** = this month till today
   - **2** = the whole previous month
   - **3** = today only (good for a quick test)

### O - Change the order
Type the report numbers in the order you want them, e.g. **13,12,1**.
Those download first; the others follow in their old order.

### H - Change how often a report downloads
1. Type the numbers of the reports to change, e.g. **2,5**.
2. Choose:
   - **1 Daily** - every day
   - **2 Monthly** - once a month, at the start of the new month (it downloads the whole previous month)
   - **3 Some days of the month** - only on the dates you type, e.g. **1,15**
   - **4 Not automatic** - never by itself; use **C** when you need it

### A - Automatic download
Switch it **on or off**, and set **how many minutes after switching on the computer** it starts.

### B - Show or hide the browser
Normally the downloads happen **invisibly** in the background, so you can keep working.
Press **B** to see the browser windows while it downloads (useful if something goes wrong).
Press **B** again to hide them.

### P - Send income to Paisa
Only if the **Paisa** money app is set up on this computer (`PAISA_DIR` in `.env`, see `.env.example`).
It adds the income from the **Job Card Invoice Statement** (labour, spares, oil) and the
**Invoice statement** (vehicles), without GST, as one entry per day per type.
This also happens **by itself** after those reports download, so you only need **P** to re-send.
Sending again never doubles anything: a day that grew is just updated.

### X - Exit
Closes the menu.

---

## Part 3 - Where are my files?

Open the tool's folder, then **downloads**. There is one folder per day:

```
downloads
  └ 2026-10-01
      ├ Stock
      │   ├ Vehicle
      │   ├ Spares
      │   ├ GMA
      │   └ Gears
      └ Reports
          ├ Booking statement
          ├ Invoice statement
          ├ Job Card Invoice Statement
          └ ... (one folder for each report)
```

Old files are never deleted or replaced.

---

## Part 4 - While it is downloading

- The downloading happens **invisibly** in the background - you can keep working normally.
- A black window shows what is happening. Some reports are slow (Parts stock can take 15-30 minutes).
- If MSD asks you to **approve a login on your phone** (or type an OTP), a browser window opens
  for that. Approve it / type it there. The tool waits for you, then continues invisibly.
- When everything is done, the black window shows **SUCCESS**.
  If something failed, it shows which report - just download that one again with **C**.
- When the automatic download starts after switching on the computer, a small window appears.
  **Close it if you want to skip the download that day.**

### REAssure Incentive Claims - please check
This report downloads claims from several years. When it finishes, the black window shows how many
rows it got and the oldest and newest date, like this:

```
'REAssure Incentive Claims': 21 rows, dates 10-Apr-2021 to 24-Aug-2026
```

Open the file and check the dates and number of rows look right.
If you see **WARNING ... data is MISSING**, tell the person who set this up.

---

## Part 5 - Problems?

| What you see | What to do |
|---|---|
| "Python is not installed" | Do **Step 1** again. Make sure you tick **"Add python.exe to PATH"**. |
| "Invalid username or password" | In the tool's folder, right-click the file **`.env`** → **Open with** → **Notepad**. Fix the login, save, close. (`PARTS_...` lines are the parts login.) |
| A report was not downloaded | Open the menu, press **C**, and download that report again. |
| MSD says the licence is missing | Ask your MSD admin. The tool can't fix this. |
| Something else | Send the person who set this up a picture from the **logs** folder (inside the tool's folder). |

### Getting a newer version
1. Download the ZIP again and extract it (**Step 2**).
2. From the **old** folder, copy the files **`.env`** and **`settings.json`** into the **new** folder.
3. Double-click **`Run MSD Automation.bat`** in the new folder once, then press **X**.
4. Delete the old folder.

---

*Your passwords are saved only on your own computer (in the `.env` file). They are never uploaded.*
