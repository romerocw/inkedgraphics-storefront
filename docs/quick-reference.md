# Storefront — Quick Reference

## The daily loop
1. Write code on the Mac (you or Claude Code) → see it at localhost:8000
2. Commit + push to GitHub
3. Deploy on the server (one command)

The server never gets edited directly. `.env` files are the one exception (secrets live only on each machine).

---

## Mac: start working
```bash
cd ~/Projects/storefront/app
source ../venv/bin/activate          # prompt shows (venv) — needed in every new tab
git pull                             # pick up anything pushed from elsewhere
python manage.py runserver           # http://localhost:8000  (Ctrl-C to stop)
```
Local console: http://localhost:8000/console/  (local login: charlieromero / your local password)

## Mac: Claude Code (in a second tab)
```bash
cd ~/Projects/storefront/app
claude            # new session (reads CLAUDE.md automatically)
claude -c         # continue the most recent session instead
```
Type requests in plain English. Useful phrases:
- "run the tests"
- "commit this with a good message and push"
- "use the Django shell to … and show me the output"
- "what would it take to …?"  (ask before building)

## Mac: commit and push by hand
```bash
git status
git add -A
git commit -m "What changed"
git push
```

## Server: deploy (after every push you want live)
```bash
ssh storefront
sudo /srv/storefront/app/deploy/deploy.sh
```
Ends with `{"status": "ok"}` and `== deployed <commit>`. Safe to run any time.

## Server: other common tasks
```bash
sudo systemctl restart storefront-uwsgi          # restart app only (e.g. after editing .env)
sudo nano /srv/storefront/.env                   # edit secrets, then restart (above)
sudo tail -n 50 /srv/storefront/logs/uwsgi.log   # app log (Django errors land here)
sudo tail -n 50 /srv/storefront/logs/httpd-error.log
sudo journalctl -u storefront-uwsgi -n 50        # if uwsgi won't start
sudo systemctl status storefront-uwsgi httpd mariadb
aws s3 ls s3://inkedgraphics-storefront/backups/ # nightly DB dumps (03:15 UTC)
sudo ls /srv/storefront/logs/emails/             # emails written to disk if SMTP unset
```

## Refresh local data from production (optional, any time)
Server:
```bash
sudo -u storefront /srv/storefront/venv/bin/python /srv/storefront/app/manage.py dumpdata stores catalog orders --indent 2 -o /tmp/storefront-data.json
sudo aws s3 sync s3://inkedgraphics-storefront/media/ /tmp/media/ --only-show-errors
sudo chmod -R a+r /tmp/storefront-data.json /tmp/media
```
Mac (venv active):
```bash
scp storefront:/tmp/storefront-data.json ../
scp -r storefront:/tmp/media ./media
python manage.py loaddata ../storefront-data.json
```

## Tests and CSS (Claude Code normally does these)
```bash
python manage.py test
../bin/tailwindcss -i assets/input.css -o stores/static/stores/site.css --minify
```

## Where things are
| What | Where |
|---|---|
| Live site | https://store.inkedgraphics.com |
| Staff console | https://store.inkedgraphics.com/console/ |
| Django admin (you only) | https://store.inkedgraphics.com/django-admin/ |
| GitHub repo | github.com/romerocw/inkedgraphics-storefront |
| Server code | /srv/storefront/app (git checkout; don't edit) |
| Server secrets | /srv/storefront/.env |
| Server logs | /srv/storefront/logs/ |
| Local project | ~/Projects/storefront/app (secrets in ~/Projects/storefront/.env) |
| Project brief | CLAUDE.md in the repo |

## If something breaks after a deploy
```bash
sudo tail -n 100 /srv/storefront/logs/uwsgi.log     # read the error
```
Roll back to the previous commit if needed:
```bash
sudo -u storefront git -C /srv/storefront/app log --oneline -5   # find the last good commit
sudo -u storefront git -C /srv/storefront/app checkout <commit>
sudo systemctl restart storefront-uwsgi
```
(Then fix forward on the Mac, push, and `deploy.sh` — it returns the server to `main`.)
