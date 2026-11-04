"""Run spike 4 over a matrix and print one line per case (+ full JSON to a file)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from spike4_a11y import run  # noqa: E402

CASES = [a.split(":") for a in sys.argv[1:]]  # name[:scale[:floating[:status]]]
results = []
for case in CASES:
    name = case[0]
    scale = int(case[1]) if len(case) > 1 and case[1] else 1
    floating = len(case) > 2 and case[2] == "f"
    status = len(case) > 3 and case[3] == "s"
    try:
        r = run(name, scale, floating, status)
    except Exception as exc:  # noqa: BLE001
        r = {"target": name, "error": f"{type(exc).__name__}: {exc}"}
    results.append(r)

    def ok(key):
        v = r.get(key)
        return "-" if v is None else ("Y" if isinstance(v, dict) and v.get("title", [None, False])[1] else "N")

    ext = r.get("A2_extents", {})
    print(f"{name:18} s={scale} fl={int(floating)} st={int(status)} | app={'Y' if r.get('A1_app') else 'N'}"
          f" nodes={r.get('A1_nodes_walked', '-')} found={','.join(r.get('A1_found', []))} | set_text={ok('A3_set_text')} rb={r.get('A3_set_text',{}).get('readback')!r}"
          f" do_action={ok('A3_do_action')} click_f3={ok('A2_click_f3')} [{r.get('A2_click_f3',{}).get('title',[''])[0]}] hit={r.get('A2_hit_extents')}@{r.get('A2_hit_point')} tk={r.get('toolkit')} margin={r.get('A2_margin_origin')} pts={r.get('A2_points')}"
          f" | frameW={ext.get('frame_window')} frameS={ext.get('frame_screen')} btnW={ext.get('button_window')}"
          f" btnS={ext.get('button_screen')} geo={r.get('sway', {}).get('geometry')} wr={r.get('sway', {}).get('window_rect')}"
          f" {('ERR ' + r['error'][:200]) if r.get('error') else ''}", flush=True)
out = os.path.expanduser("~/.hermes/cache/scratch/spike4-results.jsonl")
with open(out, "a") as f:
    for r in results:
        f.write(json.dumps(r, default=str) + "\n")
