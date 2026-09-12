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

   If PulseAudio is also installed but not running (typical for a headless
   box, since PulseAudio normally starts per-user-login-session), PortAudio
   will fail to initialize *entirely* with an error like:

   ```
   PortAudioError: Error initalizing PortAudio: Unanticipated host error
   [PaErrorCode -9999]: 'PulseAudio_Initialize: Can't connect to server'
   ```

   This is a known bug in Debian's `libportaudio2` PulseAudio host-API
   patch: instead of skipping the unreachable PulseAudio backend and
   falling back to ALSA, it aborts initialization for all backends. Since a
   systemd daemon never has a PulseAudio session to connect to, this hits
   every daemon install unless PulseAudio is removed:

   ```
   sudo systemctl --user stop pulseaudio.socket pulseaudio.service
   sudo apt-get remove --purge -y pulseaudio pulseaudio-utils
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
