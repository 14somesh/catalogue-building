import re
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

def print_full_breakdown():
    with open('logs/pipeline.log', 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    time_fmt = '%Y-%m-%d %H:%M:%S'
    entries = []
    for line in lines:
        m = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[([A-Z]+)\] \[([^\]]+)\] (.*)', line.strip())
        if m:
            ts_str, level, module, msg = m.groups()
            ts = datetime.strptime(ts_str, time_fmt)
            if ts.year == 2026 and ts.month == 9 and ts.day == 1 and ts.hour >= 17:
                entries.append((ts, level, module, msg))

    run_starts = [i for i, e in enumerate(entries) if "Starting autonomous pipeline run for brand 'Urbn'" in e[3]]
    for run_idx, start_i in enumerate(run_starts):
        end_i = run_starts[run_idx + 1] if run_idx + 1 < len(run_starts) else len(entries)
        run_entries = entries[start_i:end_i]
        start_t = run_entries[0][0]
        end_t = run_entries[-1][0]
        dur = (end_t - start_t).total_seconds()
        print(f"\n================================================================================")
        print(f"PIPELINE RUN #{run_idx + 1} | Start: {start_t.strftime('%H:%M:%S')} | End: {end_t.strftime('%H:%M:%S')} | Total Elapsed: {dur:.1f}s ({dur/60:.2f} min)")
        print(f"================================================================================")
        
        for e in run_entries:
            # Print significant milestone lines
            msg = e[3]
            if any(k in msg for k in ['[PB-URB', 'Tier', 'Vision', 'draft copy', 'Downloading', 'Saved image', 'Passed validation', 'SKIPPED', 'HALT', 'Generated run report']):
                print(f"  [{e[0].strftime('%H:%M:%S')}] [{e[2]:15s}] {msg}")

if __name__ == '__main__':
    print_full_breakdown()
