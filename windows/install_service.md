# Running as a Windows service (NSSM)

Using [NSSM](https://nssm.cc/) avoids writing Windows service boilerplate
(`pywin32`) for this MVP -- it just wraps the Python script and restarts it
if it exits.

1. Install Python 3.10+ and the project dependencies:

   ```
   py -3 -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

2. Run the setup wizard once to generate `config.yaml`:

   ```
   .venv\Scripts\python -m client.setup_wizard --out config.yaml
   ```

3. Download NSSM and install the service (run as Administrator):

   ```
   nssm install ChamberClient "C:\path\to\buckyball-chamber\.venv\Scripts\python.exe" "-m client.main --config C:\path\to\buckyball-chamber\config.yaml"
   nssm set ChamberClient AppDirectory "C:\path\to\buckyball-chamber"
   nssm set ChamberClient AppExit Default Restart
   nssm start ChamberClient
   ```

4. Check logs at `logs\chamber-client.log` inside the project directory
   (configurable via `logging.log_dir` in `config.yaml`), or:

   ```
   nssm status ChamberClient
   ```

5. To stop/remove:

   ```
   nssm stop ChamberClient
   nssm remove ChamberClient confirm
   ```
