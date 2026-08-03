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

1. Set up the SFTP server: `docs/server_setup.md`.
2. On the chamber machine, install dependencies and run the setup wizard to
   pick audio devices/channels and write `config.yaml`:

   ```
   pip install -r requirements.txt
   python -m client.setup_wizard
   ```

3. Install the client as a service so it survives reboots/crashes:
   - Raspberry Pi / Linux: `systemd/README.md`
   - Windows: `windows/install_service.md`

4. From wherever you have a recording to play, upload it:

   ```
   python -m uploader.upload_job my_track.wav --config config.yaml
   ```

   This prints a job ID. Once the chamber has processed it, the recording
   appears under the same job ID in `recordings/` on the SFTP server --
   download it with any SFTP client.

## Notes

- WAV only for both playback and recordings, in this MVP.
- Input channel count is configurable independently of output channel count,
  so an ambisonic microphone (4+ channels) is supported directly.
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
