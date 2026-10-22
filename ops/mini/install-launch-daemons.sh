#!/bin/zsh
# Turn hermes's com.feldenkrais.* launch agents into system launch daemons that
# run as hermes, so the Mini's servers and workers start at boot with nobody
# logged in. A launch agent starts only inside its user's login session, so
# after a reboot nothing ran until someone logged in as hermes.
#
# Run once on the Mini, as an administrator:
#
#   sudo /Users/hermes/ops/install-launch-daemons.sh
#   /Users/hermes/ops/install-launch-daemons.sh --dry-run [label]   # as hermes, no changes;
#                                                       # with a label, prints that daemon's plist
#
# Each daemon keeps its agent's program, schedule, logs and environment, and
# adds UserName/GroupName plus the HOME, USER, LOGNAME and TMPDIR a login
# session would have provided. A job disabled as an agent stays disabled. The
# agent plists move to ~/Library/LaunchAgents/retired-to-daemons/ so that a
# hermes login cannot start a second copy. Safe to re-run: agents already
# retired are skipped, and a daemon is replaced from the newest agent copy only
# when the agent is still in place.
set -euo pipefail

user=hermes
home=/Users/$user
agents=$home/Library/LaunchAgents
retired=$agents/retired-to-daemons
dry_run=0
[[ ${1:-} == --dry-run ]] && dry_run=1

if (( ! dry_run )) && (( EUID != 0 )); then
  print -u2 "run with sudo (or pass --dry-run)"
  exit 1
fi

uid=$(id -u $user)
if (( EUID == 0 )); then
  tmpdir=$(sudo -u $user getconf DARWIN_USER_TEMP_DIR)
else
  tmpdir=$(getconf DARWIN_USER_TEMP_DIR)
fi
disabled_labels=$(launchctl print-disabled user/$uid 2>/dev/null \
  | awk -F'"' '/=> (disabled|true)/ { print $2 }')
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

agent_files=($agents/com.feldenkrais.*.plist(N))
(( ${#agent_files} )) || { print "no com.feldenkrais agents left in $agents; nothing to do"; exit 0; }

for agent in $agent_files; do
  label=${agent:t:r}
  daemon=/Library/LaunchDaemons/$label.plist
  staged=$work/$label.plist

  cp "$agent" "$staged"
  plutil -replace UserName -string "$user" "$staged"
  plutil -replace GroupName -string staff "$staged"
  plutil -extract EnvironmentVariables xml1 -o /dev/null "$staged" >/dev/null 2>&1 \
    || plutil -insert EnvironmentVariables -dictionary "$staged"
  plutil -replace EnvironmentVariables.HOME -string "$home" "$staged"
  plutil -replace EnvironmentVariables.USER -string "$user" "$staged"
  plutil -replace EnvironmentVariables.LOGNAME -string "$user" "$staged"
  plutil -replace EnvironmentVariables.TMPDIR -string "$tmpdir" "$staged"
  plutil -lint -s "$staged"

  disabled=0
  print -r -- "$disabled_labels" | grep -qx "$label" && disabled=1

  if (( dry_run )); then
    print "would install $daemon$( (( disabled )) && print ' (disabled)')"
    [[ ${2:-} == $label ]] && plutil -p "$staged"
    continue
  fi

  # Stop the agent copy wherever it is loaded, then install and load the daemon.
  launchctl bootout gui/$uid/$label 2>/dev/null || true
  launchctl bootout user/$uid/$label 2>/dev/null || true
  launchctl bootout system/$label 2>/dev/null || true

  install -o root -g wheel -m 644 "$staged" "$daemon"
  if (( disabled )); then
    launchctl disable system/$label
  else
    launchctl enable system/$label
    launchctl bootstrap system "$daemon"
  fi

  mkdir -p "$retired"
  chown $user:staff "$retired"
  mv "$agent" "$retired/"
  print "installed $daemon$( (( disabled )) && print ' (disabled, not loaded)')"
done

(( dry_run )) || print "\nDone. Check with: launchctl print system/com.feldenkrais.analyzer-web | head"
