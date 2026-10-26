#!/bin/zsh
# Run one heavy command under a watchdog and keep the Mac awake while it runs.
# Usage: guarded_run.sh <log_dir> <command> [args...]
# Every 5 s it logs disk, the command's memory (whole process tree), reclaimable system memory and
# swap to <log_dir>/monitor.log and kills the command when any limit is crossed (exit code 99).
# Limits (override via environment). Background: R-083, a run without working limits froze the Mac.
case "${1:-}" in -h|--help|"") sed -n '2,/^case /p' $0 | sed '$d' | sed 's/^# \{0,1\}//'; exit 0;; esac
set -u
LOGDIR=$1; shift
MAX_RSS_MB=${MAX_RSS_MB:-8000}       # memory of the command and its children
MIN_AVAIL_MB=${MIN_AVAIL_MB:-2500}   # free + inactive + speculative + purgeable
MAX_SWAP_GROWTH_MB=${MAX_SWAP_GROWTH_MB:-3000}
MIN_FREE_GB=${MIN_FREE_GB:-15}
mkdir -p "$LOGDIR"; MON="$LOGDIR/monitor.log"
swap_mb() { sysctl -n vm.swapusage | awk '{gsub("M","",$6); printf "%d", $6}'; }
SWAP0=$(swap_mb)
caffeinate -i "$@" > "$LOGDIR/stdout.log" 2>&1 &
PID=$!
tree_rss_mb() {  # sum RSS over PID and all descendants
  ps -axo pid=,ppid=,rss= | awk -v root=$PID '{pp[$1]=$2; r[$1]=$3} END{for(p in pp){q=p; while(q!=root && q in pp && q!=1) q=pp[q]; if(q==root) s+=r[p]} printf "%d", s/1024}'
}
echo "$(date '+%F %T') start pid=$PID limits rss=$MAX_RSS_MB avail=$MIN_AVAIL_MB swap_growth=$MAX_SWAP_GROWTH_MB disk=$MIN_FREE_GB cmd=$1" >> "$MON"
KILLED=0
while kill -0 $PID 2>/dev/null; do
  free_gb=$(df -g "$LOGDIR" | awk 'NR==2{print $4}')
  rss=$(tree_rss_mb)
  avail=$(vm_stat | awk '/Pages (free|inactive|speculative|purgeable)/{gsub("\\.","",$NF); s+=$NF} END{printf "%d", s*16/1024}')
  swapg=$(( $(swap_mb) - SWAP0 ))
  echo "$(date '+%F %T') disk_free_gb=$free_gb rss_mb=$rss avail_mb=$avail swap_growth_mb=$swapg" >> "$MON"
  if [ "$free_gb" -lt "$MIN_FREE_GB" ] || [ "$rss" -gt "$MAX_RSS_MB" ] || [ "$avail" -lt "$MIN_AVAIL_MB" ] || [ "$swapg" -gt "$MAX_SWAP_GROWTH_MB" ]; then
    echo "$(date '+%F %T') WATCHDOG KILL" >> "$MON"
    pkill -KILL -P $PID 2>/dev/null; kill -KILL $PID 2>/dev/null; KILLED=1; break
  fi
  sleep 5
done
wait $PID 2>/dev/null; RC=$?
[ $KILLED = 1 ] && RC=99
echo "$(date '+%F %T') end rc=$RC peak_rss_mb=$(awk -F'rss_mb=' 'NF>1{split($2,a," "); if(a[1]+0>m)m=a[1]+0} END{print m+0}' "$MON")" >> "$MON"
exit $RC
