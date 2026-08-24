# How to run Blocketvakten

## Reproduce uncommitted artifacts

None required. The app is self-contained (Python 3.10+ standard library only,
no dependencies or env files). The SQLite database is created automatically at
`data/blocketvakten.db` on first start.

## Run the server

```bash
python app.py
```

Serves the web app and API on http://127.0.0.1:8080 by default (override with
`BLOCKETVAKTEN_PORT`). The background scheduler checks active searches every
60 seconds; there are no searches by default, so it idles harmlessly.

Detach on Windows (for the Preview tab):

```powershell
powershell -NoProfile -Command "(Start-Process -FilePath 'C:\Users\virre\AppData\Local\Programs\Python\Python310\python.exe' -ArgumentList '-u','app.py' -WorkingDirectory 'D:\Freebuff' -RedirectStandardOutput '<log>' -RedirectStandardError '<log>.err' -WindowStyle Hidden -PassThru).Id"
```
