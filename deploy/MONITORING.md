# Monitoring

The server ships its logs to CloudWatch Logs, and one alarm tells us if the store
schedule job (`lifecycle_tick`) stops running. If it stops, stores don't open or
close on time.

## What's shipped

`deploy/cloudwatch-agent.json` sends these files to log group **`/storefront/prod`**
(30-day retention), one stream per file, named `<instance id>/<file name>`:

| File in `/srv/storefront/logs/` | What's in it |
|---|---|
| `uwsgi.log` | the Django app |
| `httpd-error.log` | Apache errors |
| `cron-send_outbox.log` | one line a minute: `send_outbox: sent=0 failed=0 remaining=0` |
| `cron-lifecycle_tick.log` | one line every 5 minutes: `lifecycle_tick: opened=0 closed=0 checked=12` |

`deploy.sh` installs the config as
`/opt/aws/amazon-cloudwatch-agent/etc/storefront-cloudwatch-agent.json` and applies it
(`amazon-cloudwatch-agent-ctl -a fetch-config`, which also restarts the agent) only when the
file changed. Edit the copy in the repo, never the one on the server.

## One-time server setup

1. **Install the agent.** Amazon Linux: `sudo dnf install -y amazon-cloudwatch-agent`
   (other distributions: AWS's "Installing the CloudWatch agent" page has the package).
   Until it's installed, every deploy prints `WARNING: CloudWatch agent not installed`.
2. **Give the instance permission.** In IAM, attach the AWS-managed policy
   **`CloudWatchAgentServerPolicy`** to the EC2 instance's role
   (EC2 → the instance → Security → IAM role).
3. **Deploy.** The `== cloudwatch` step applies the config. Check the agent is happy:
   `sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a status`
   (should say `"status": "running"`).
4. **Check the retention.** In CloudWatch → Log groups, `/storefront/prod` should show
   "1 month" retention. The agent sets it when it creates the group, if the role is allowed
   to (`logs:PutRetentionPolicy`). If it says "Never expire", set it by hand: select the
   group → Actions → Edit retention setting → 1 month.

The config uses `-m ec2`. If the server ever isn't EC2, change it to `-m onPremise` in
`deploy.sh` and give the agent credentials instead of an instance role.

## The alarm: "lifecycle tick hasn't run in 15 minutes"

`lifecycle_tick` runs every 5 minutes and always prints its summary line, even when
nothing changed. So 15 minutes without a line means it has missed 3 runs in a row.

### 1. Where alerts go (skip if you already have an SNS topic)

SNS → Topics → **Create topic** → Standard, name `storefront-alerts` → Create.
Then **Create subscription** → Protocol *Email*, your address → Create, and click the
confirmation link in the email AWS sends.

### 2. Metric filter: count the summary lines

CloudWatch → Log groups → **`/storefront/prod`** → **Metric filters** tab → **Create metric filter**.

- **Filter pattern:** `"lifecycle_tick:"`, with the double quotes, because the colon
  needs quoting. **Test pattern** against the `cron-lifecycle_tick.log` stream should
  match its lines. Next.
- **Filter name:** `lifecycle-tick-runs`
- **Metric namespace:** `Storefront`
- **Metric name:** `LifecycleTickRuns`
- **Metric value:** `1`
- **Default value:** leave **empty**. Minutes with no line should be *missing data*, not 0;
  the alarm below treats missing data as breaching.
- **Unit:** Count. Next → **Create metric filter**.

A metric filter only counts lines that arrive after it exists, so the metric has no
history at first.

### 3. Alarm on it

On the same Metric filters tab, tick `lifecycle-tick-runs` → **Create alarm**.
(Or: CloudWatch → Alarms → Create alarm → Select metric → Storefront → Metrics with no
dimensions → `LifecycleTickRuns`.)

- **Statistic:** Sum
- **Period:** 15 minutes
- **Threshold type:** Static. **Whenever LifecycleTickRuns is…** Lower than **1**
- **Additional configuration → Datapoints to alarm:** 1 out of 1
- **Missing data treatment:** **Treat missing data as bad (breaching threshold)**.
  This matters: when the job stops, there are no datapoints at all.
- Next → **Notification:** In alarm → your SNS topic. Also add one for **OK**, so you
  hear when it recovers.
- **Alarm name:** `storefront-lifecycle-tick-stopped`. Description, e.g. "No
  lifecycle_tick summary line in 15 minutes: stores won't open/close on schedule. Check
  /srv/storefront/logs/cron-lifecycle_tick.log and /etc/cron.d/storefront." → Create alarm.

It will sit in *Insufficient data* or *In alarm* until the first lines arrive after the
filter was created; within about 15 minutes of a working job it should turn *OK*.

### 4. Test it

- **Notification only:**
  `aws cloudwatch set-alarm-state --alarm-name storefront-lifecycle-tick-stopped --state-value ALARM --state-reason "test"`.
  You should get the email; the alarm returns to OK on its next evaluation.
- **For real:** on the server, comment out the `lifecycle_tick` line in
  `/etc/cron.d/storefront`. The alarm fires within about 15–20 minutes. Uncomment it (or
  just deploy, which reinstalls the file) and it returns to OK.

### Optional: the same for email sending

Same recipe with pattern `"send_outbox:"`, metric `SendOutboxRuns`, period **5 minutes**
(it runs every minute), alarm name `storefront-send-outbox-stopped`.

## When the alarm fires

1. `sudo tail -n 20 /srv/storefront/logs/cron-lifecycle_tick.log`. A Python traceback
   means the job is crashing; the last summary line shows when it last worked.
2. `cat /etc/cron.d/storefront`: is the line there? `sudo systemctl status crond` (`cron` on
   Debian/Ubuntu): is cron running?
3. A stuck earlier run holds the lock: `ps aux | grep lifecycle_tick`.
4. Run it by hand to see the error:
   `sudo -u storefront /srv/storefront/venv/bin/python /srv/storefront/app/manage.py lifecycle_tick`.
5. The console dashboard's **System** box (owners and managers) shows when each job last ran.
