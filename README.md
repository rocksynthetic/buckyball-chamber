# Buckyball Chamber Playback/Recording

Asynchronous playback + recording for a remote acoustic chamber. Upload a
WAV file, the on-site client plays it through the chamber's speakers while
recording the acoustic response, and uploads the recording back -- all over
a plain SFTP server, tolerant of intermittent connectivity.

## How it works

- An **SFTP server** (a small VPS running OpenSSH, see `docs/server_setup.md`)
  acts as the job queue: `incoming/` -> `processing/` -> `recordings/` +
  `done/` (or `failed/`).
- The **uploader CLI** (`uploader/upload_job.py`) drops a WAV file into
  `incoming/` with an atomic upload-then-rename, so the client never sees a
  half-uploaded file.
- The **chamber client** (`client/main.py`), a daemon running on-site, polls
  for new jobs, downloads them, plays each through the chamber's speakers
  while simultaneously recording (for the playback's duration plus a fixed
  tail, to catch reverb/decay), and uploads the result back. It only needs
  connectivity to discover jobs and ship results -- playback/recording never
  blocks on the network.

## Quick start

1. Set up the SFTP server: `sudo ./scripts/setup_server.sh <pubkey-file-or-string> ...`
   (see `docs/server_setup.md` for details/manual steps).

2. **On the chamber machine** (Linux/Raspberry Pi), run the one-shot setup
   script. It clones this repo, installs system + Python dependencies, and
   runs the device/SFTP setup wizard. Since the repo is private, the machine
   needs an SSH key already added to GitHub with access to it first (a
   deploy key is enough -- read access only). Once that's in place:

   ```
   git clone git@github.com:rocksynthetic/buckyball-chamber.git ~/buckyball-chamber
   ~/buckyball-chamber/scripts/setup_client.sh --install-service
   ```

   (re-running `scripts/setup_client.sh` later just pulls updates and
   reuses the existing config -- pass `--reconfigure` to redo the wizard, or
   `--dir`/`--repo-url` to override the defaults). Drop `--install-service`
   to skip installing it as a systemd service and just test it in the
   foreground first (see `systemd/README.md` / `windows/install_service.md`
   for manual/Windows install instead).

3. **On the machine you'll upload from** (e.g. your Mac), same idea but with
   the lighter uploader-only script (no audio libraries needed):

   ```
   git clone git@github.com:rocksynthetic/buckyball-chamber.git ~/buckyball-chamber
   ~/buckyball-chamber/scripts/setup_uploader.sh
   ```

   (if you're already inside a clone of this repo, as on the machine this
   was developed on, just run `./scripts/setup_uploader.sh` directly).

   This also installs a `chamber` command to `~/.local/bin` that wraps the
   uploader with this install's `config.yaml` baked in, so daily use doesn't
   need `.venv/bin/python -m uploader.upload_job --config ...` typed out each
   time (the script tells you if `~/.local/bin` needs adding to your PATH).

4. From that machine, send a job -- uploads the file and waits for the
   finished recording, in one step:

   ```
   chamber send my_track.wav
   ```

   Or do the two steps separately if you don't want to wait around:

   ```
   chamber upload my_track.wav
   ```

   This prints a job ID and remembers it locally as "the last upload" (in a
   small state file next to `config.yaml`), so a later `download` with no
   arguments knows what to fetch:

   ```
   chamber download
   ```

   (No `chamber` on your PATH, or want to call it directly? Use
   `.venv/bin/python -m uploader.upload_job <upload|download|send> ... --config config.yaml`
   from inside `~/buckyball-chamber` instead -- same commands, same
   behavior.)

   **Waiting mode**: `send` and `download` don't just fail if the recording
   isn't ready yet -- both poll until the chamber finishes processing the
   job, downloading it the moment it appears. What this looks like:

   - Downloads immediately if the recording is already sitting in
     `recordings/` on the server (e.g. you ran `download` again after
     Ctrl+C'ing an earlier wait).
   - Otherwise prints `Recording not ready yet; waiting (polling every 10s,
     Ctrl+C to stop)...` once, then re-checks the server every
     `--poll-interval` seconds (default 10) without repeating that message
     each time.
   - Waits indefinitely by default. Pass `--timeout SECONDS` to give up
     after a while instead (raises an error and exits 1 rather than hanging
     forever, e.g. for use in a script).
   - Ctrl+C at any point during the wait exits cleanly (prints "Stopped
     waiting." and exits 1) -- it doesn't cancel the chamber-side job, so
     running `download` again later still picks up the result once it's
     ready.
   - If the job instead shows up in `failed/` on the server (e.g. the audio
     device rejected the file's sample rate), `download` stops waiting
     immediately with an error naming the failed path, instead of polling
     forever for a recording that will never arrive.
   - A dropped/flaky connection to the SFTP server during the wait is
     retried automatically (prints "connection issue, will retry: ...")
     rather than aborting the wait.
   - Saved to `./<job_id>__<name>__recording.wav` by default; pass `--out
     PATH` to choose a different destination.

   Pass `--job-id ... --name ...` to fetch a specific past job instead of
   the last one uploaded from this machine (`--name` can be omitted if
   `--job-id` matches that last recorded upload).

## Notes

- WAV only for both playback and recordings, in this MVP.
- Input channel count is configurable independently of output channel count,
  so an ambisonic microphone (4+ channels) is supported directly.
- Each job opens the audio device at its own file's sample rate rather than
  a fixed configured rate -- this relies on the client having exclusive
  access to the device (no OS mixer resampling everything to one shared
  rate). If the device can't support a given file's rate, the job fails
  with a clear error and follows the normal retry/failed path.
- Local disk usage stays bounded: a job's local files are deleted as soon as
  its recording is confirmed uploaded; failed-job files and orphaned temp
  files are swept on a TTL (see `client.failed_job_retention_days` /
  `temp_file_ttl_days` in `config.yaml`).
- Single chamber, one job at a time, no web UI -- see the "explicitly
  punted" list in the design doc for what's deliberately out of scope.

## Development

```
pip install -r requirements-dev.txt
pytest
```
