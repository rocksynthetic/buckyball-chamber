# systemd setup (Raspberry Pi / Linux laptop)

`../scripts/setup_client.sh --install-service` does all of this
automatically, running as your normal login user out of `~/buckyball-chamber`
rather than a dedicated `chamber` system user under `/opt`. Use that unless
you specifically want the more locked-down dedicated-user layout below.

1. Copy the project to `/opt/buckyball-chamber` and create a virtualenv:

   ```
   sudo mkdir -p /opt/buckyball-chamber
   sudo cp -r . /opt/buckyball-chamber
   cd /opt/buckyball-chamber
   sudo python3 -m venv .venv
   sudo .venv/bin/pip install -r requirements.txt
   ```

2. On Raspberry Pi / Debian-based systems, install the system PortAudio
   library that `sounddevice`'s Linux wheel links against:

   ```
   sudo apt install libportaudio2
   ```

3. Run the setup wizard once to generate `config.yaml`:

   ```
   sudo .venv/bin/python -m client.setup_wizard --out /opt/buckyball-chamber/config.yaml
   ```

4. Create a dedicated `chamber` user (or adjust `User=` in the unit file to
   an existing account that has access to the audio devices):

   ```
   sudo useradd --system --home /opt/buckyball-chamber chamber
   sudo usermod -aG audio chamber
   sudo chown -R chamber:chamber /opt/buckyball-chamber
   ```

5. Install and start the service:

   ```
   sudo cp systemd/chamber-client.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now chamber-client
   sudo journalctl -u chamber-client -f
   ```
