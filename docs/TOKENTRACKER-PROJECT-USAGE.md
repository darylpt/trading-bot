# TokenTracker project usage

This documents the setup and recovery procedure for making an oh-my-pi/OMP repository appear in TokenTracker's **Project Usage** view.

## How project attribution works

TokenTracker records global usage and project usage separately.

- Global usage comes from the agent session files.
- Project usage is attributed from the session working directory's Git remote.
- The project key is derived from the remote path. For example:
  `https://github.com/darylpt/trading-bot.git` becomes `darylpt/trading-bot`.
- A repository without a parseable remote can appear in global usage while being absent from Project Usage.

The remote does not need to be added to the repository history. It only needs to be present in `.git/config`.

## Initial setup on another PC

Run these commands from the repository root in PowerShell:

```powershell
git remote -v

# Add a remote if none exists.
git remote add origin https://github.com/<owner>/<repository>.git

# Or update an existing origin.
git remote set-url origin https://github.com/<owner>/<repository>.git
```

Confirm that `.git/config` contains an entry like:

```ini
[remote "origin"]
    url = https://github.com/<owner>/<repository>.git
```

Make sure TokenTracker is installed and its oh-my-pi integration is enabled. Verify the local detector:

```powershell
node "$env:USERPROFILE\.tokentracker\tracker\app\bin\tracker.js" status
```

The output should include an oh-my-pi passive reader and a nonzero number of session files.

Run a full local sync:

```powershell
node "$env:USERPROFILE\.tokentracker\tracker\app\bin\tracker.js" sync
```

Then refresh the dashboard with `Ctrl+F5`.

## Important: adding the remote after previous sessions exist

If TokenTracker scanned the repository before the remote was added, a normal sync may still not create the project. TokenTracker has a separate project cursor. It can already be at end-of-file for the old sessions, so adding the remote does not automatically re-evaluate them.

Close the TokenTracker dashboard or stop its local server before changing its cursor state.

The following PowerShell procedure resets only the project-attribution cursors for the current repository. It does not reset global usage cursors, so global usage is not counted again.

```powershell
$repoRoot = (Get-Location).Path
$tempScript = Join-Path $env:TEMP "tokentracker-reset-project-attribution.mjs"

@'
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(process.argv[2] || process.cwd());
const home = process.env.USERPROFILE || process.env.HOME;
if (!home) throw new Error("USERPROFILE/HOME is not set");

const cursorPath = path.join(home, ".tokentracker", "tracker", "cursors.json");
const cursors = JSON.parse(fs.readFileSync(cursorPath, "utf8"));
const normalizedRepo = path.normalize(repoRoot).toLowerCase();
const encodedRepo = `--${repoRoot.replace(/[\\/:]/g, "-")}--`.toLowerCase();
const providerNames = ["omp", "pi"];
let resetFiles = 0;
let removedSeenIds = 0;
const backupPath = `${cursorPath}.before-project-reset-${Date.now()}`;
fs.copyFileSync(cursorPath, backupPath);

function normalized(value) {
  return path.normalize(value).toLowerCase();
}

function isRepositorySessionFile(filePath) {
  const normalizedFile = normalized(filePath);
  const normalizedRoot = normalized(path.join(home, ".omp", "agent", "sessions"));
  const normalizedPiRoot = normalized(path.join(home, ".pi", "agent", "sessions"));
  const inAgentSessions = normalizedFile.startsWith(`${normalizedRoot}${path.sep}`)
    || normalizedFile.startsWith(`${normalizedPiRoot}${path.sep}`);
  if (!inAgentSessions) return false;

  // OMP session-directory encoding is --<cwd with separators replaced by ->--.
  const portableFile = filePath.replaceAll("\\", "/").toLowerCase();
  const portableEncoded = encodedRepo.replaceAll("\\", "/");
  if (portableFile.includes(`/${portableEncoded}/`)) return true;

  // Fallback for layouts or future versions that preserve cwd in the header.
  try {
    const prefix = fs.readFileSync(filePath, "utf8").slice(0, 65536);
    for (const line of prefix.split(/\r?\n/)) {
      if (!line.includes('"type":"session"')) continue;
      try {
        const entry = JSON.parse(line);
        if (entry?.type === "session" && typeof entry.cwd === "string") {
          return normalized(entry.cwd) === normalizedRepo;
        }
      } catch {}
    }
  } catch {}
  return false;
}

function collectUsageIds(filePath, ids) {
  try {
    for (const line of fs.readFileSync(filePath, "utf8").split(/\r?\n/)) {
      if (!line.includes('"type":"message"')) continue;
      try {
        const entry = JSON.parse(line);
        if (
          entry?.type === "message" &&
          entry?.message?.role === "assistant" &&
          entry?.message?.usage &&
          typeof entry.id === "string"
        ) {
          ids.add(entry.id);
        }
      } catch {}
    }
  } catch {}
}

for (const providerName of providerNames) {
  const provider = cursors[providerName];
  if (!provider || typeof provider !== "object") continue;
  const offsets = provider.projectFileOffsets;
  if (!offsets || typeof offsets !== "object") continue;

  const targetIds = new Set();
  for (const filePath of Object.keys(offsets)) {
    if (!isRepositorySessionFile(filePath)) continue;
    collectUsageIds(filePath, targetIds);
    delete offsets[filePath];
    resetFiles += 1;
  }

  if (Array.isArray(provider.projectSeenIds) && targetIds.size > 0) {
    const before = provider.projectSeenIds.length;
    provider.projectSeenIds = provider.projectSeenIds.filter((id) => !targetIds.has(id));
    removedSeenIds += before - provider.projectSeenIds.length;
  }
  provider.updatedAt = new Date().toISOString();
}

cursors.updatedAt = new Date().toISOString();
fs.writeFileSync(cursorPath, JSON.stringify(cursors, null, 2), "utf8");
console.log(JSON.stringify({ cursorPath, backupPath, resetFiles, removedSeenIds }, null, 2));
'@ | Set-Content -Encoding UTF8 $tempScript

node $tempScript $repoRoot
Remove-Item $tempScript
```

Run the OMP-only rescan after the reset:

```powershell
node "$env:USERPROFILE\.tokentracker\tracker\app\bin\tracker.js" sync --auto --from-notify --source omp
```

Refresh the dashboard. The project should now appear under its remote-derived key.

## Verification

Check the local project queue for the repository key:

```powershell
Select-String `
  -Path "$env:USERPROFILE\.tokentracker\tracker\project.queue.jsonl" `
  -Pattern "<owner>/<repository>"
```

For this repository, the expected key is currently:

```text
darylpt/trading-bot
```

The queue rows should contain both fields:

```json
{
  "project_ref": "https://github.com/darylpt/trading-bot",
  "project_key": "darylpt/trading-bot"
}
```

## Troubleshooting

### The repository is in global usage but not Project Usage

Check the remote first:

```powershell
git remote get-url origin
```

Then check whether the project queue contains the expected key. If the remote was added after previous sessions were scanned, run the targeted cursor reset above.

### The project queue contains the key but the dashboard does not

Refresh with `Ctrl+F5`. If the dashboard server has been running for a long time, restart TokenTracker and reopen the dashboard.

### The remote is local-only

TokenTracker rejects `file://` and filesystem remotes for project attribution. Use a hosted HTTPS or SSH remote. The remote URL is used as the local project identity; repository contents are not required for this procedure.

### The session source is `pi` instead of `omp`

The reset script checks both `~/.omp/agent/sessions` and `~/.pi/agent/sessions`. Run the matching source sync if the status output identifies the provider as `pi`:

```powershell
node "$env:USERPROFILE\.tokentracker\tracker\app\bin\tracker.js" sync --auto --from-notify --source pi
```

## State and safety notes

- TokenTracker state is under `%USERPROFILE%\.tokentracker\tracker`, not in this repository.
- The reset script creates a timestamped backup of `cursors.json` before editing it.
- Only project cursors for the selected repository are reset.
- Global token cursors are preserved, preventing global usage double-counting.
- Do not delete all of `cursors.json` unless you intentionally want to rebuild every provider and every project.
