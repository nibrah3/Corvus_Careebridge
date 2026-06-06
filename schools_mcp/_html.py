"""
Generate a self-contained HTML report of qualifying schools,
categorised by CareerBridge criteria. No external dependencies.
"""
from __future__ import annotations

import html as _html
import json
from datetime import datetime
from pathlib import Path

CRITERIA_META: dict[str, dict] = {
    "community_college":      {"emoji": "🏫", "label": "Community College"},
    "no_id_verification":     {"emoji": "🪪", "label": "No ID Required"},
    "no_transcript_required": {"emoji": "📄", "label": "No Transcript"},
    "monthly_enrollment":     {"emoji": "📅", "label": "Monthly Enrollment"},
    "instant_acceptance":     {"emoji": "⚡", "label": "Instant Acceptance"},
    "monthly_refund":         {"emoji": "💸", "label": "Monthly Refund"},
}

_SCORE_COLOR = {6:"#27ae60",5:"#2ecc71",4:"#3498db",3:"#e67e22",2:"#e74c3c",1:"#c0392b",0:"#7f8c8d"}


def _card(s: dict) -> str:
    name     = _html.escape(s.get("name", ""))
    url      = _html.escape(s.get("url", "") or "")
    enroll   = _html.escape(s.get("enrollment_url", "") or "")
    city     = _html.escape(s.get("city", "") or "")
    state    = _html.escape(s.get("state", "") or "")
    score    = int(s.get("criteria_score", 0))
    filters  = s.get("filters") or []
    df       = " ".join(f for f in filters if f in CRITERIA_META)
    color    = _SCORE_COLOR.get(score, "#7f8c8d")

    badges = "".join(
        f'<span class="b b-{f}">{CRITERIA_META[f]["emoji"]} {CRITERIA_META[f]["label"]}</span>'
        for f in filters if f in CRITERIA_META
    )
    loc  = f"{city}, {state}".strip(", ") if city or state else ""
    link = f'<a href="{url}" target="_blank" rel="noopener">Visit</a>' if url else ""
    if enroll:
        link += f' <a href="{enroll}" target="_blank" rel="noopener">Apply</a>'

    return (
        f'<div class="card" data-f="{df}" data-n="{name.lower()}">'
        f'<div class="ch"><span class="cn">{name}</span>'
        f'<span class="sc" style="background:{color}">{score}/6</span></div>'
        + (f'<div class="loc">📍 {loc}</div>' if loc else "")
        + f'<div class="badges">{badges}</div>'
        + (f'<div class="lnk">{link}</div>' if link else "")
        + "</div>\n"
    )


def generate(schools: list[dict], output_path: str) -> str:
    """
    Write a self-contained HTML report to output_path.
    schools: list of school dicts with criteria fields.
    Returns the absolute path written.
    """
    counts = {c: sum(1 for s in schools if c in (s.get("filters") or [])) for c in CRITERIA_META}
    total  = len(schools)
    ts     = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    filter_btns = f'<button class="fb active" onclick="filt(\'\')" id="btn-all">All ({total})</button>\n'
    for c, m in CRITERIA_META.items():
        filter_btns += (
            f'<button class="fb" onclick="filt(\'{c}\')" id="btn-{c}">'
            f'{m["emoji"]} {m["label"]} ({counts[c]})</button>\n'
        )

    cards_html = "".join(_card(s) for s in schools)

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CareerBridge Schools · {ts}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f0f1a;color:#e0e0f0;min-height:100vh}}
header{{background:#16213e;padding:20px 24px;border-bottom:1px solid #1a3a5c;position:sticky;top:0;z-index:10}}
.htop{{display:flex;align-items:center;gap:16px;flex-wrap:wrap}}
h1{{font-size:1.4rem;color:#4fc3f7;white-space:nowrap}}
.stat{{color:#90caf9;font-size:.9rem}}
#search{{background:#0d1b2a;border:1px solid #1e3a5f;color:#e0e0f0;border-radius:8px;
         padding:8px 14px;font-size:.9rem;width:260px;outline:none}}
#search:focus{{border-color:#4fc3f7}}
.fbar{{background:#12192b;padding:12px 24px;display:flex;gap:8px;flex-wrap:wrap;
       border-bottom:1px solid #1a3a5c;position:sticky;top:73px;z-index:9}}
.fb{{background:#1a2f4a;border:1px solid #1e3a5f;color:#b0c4de;border-radius:20px;
     padding:6px 14px;font-size:.8rem;cursor:pointer;transition:all .15s;white-space:nowrap}}
.fb:hover{{background:#1e3a5f;color:#fff}}
.fb.active{{background:#0d47a1;border-color:#1565c0;color:#fff}}
#count{{padding:10px 24px;color:#78909c;font-size:.85rem}}
#grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));
       gap:14px;padding:16px 24px 40px}}
.card{{background:#16213e;border:1px solid #1a3a5c;border-radius:10px;
       padding:14px 16px;display:flex;flex-direction:column;gap:8px;
       transition:border-color .15s}}
.card:hover{{border-color:#4fc3f7}}
.ch{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}}
.cn{{font-weight:600;font-size:.95rem;color:#e3f2fd;line-height:1.3}}
.sc{{font-size:.75rem;font-weight:700;color:#fff;border-radius:10px;
     padding:2px 8px;white-space:nowrap;flex-shrink:0;margin-top:2px}}
.loc{{font-size:.78rem;color:#78909c}}
.badges{{display:flex;flex-wrap:wrap;gap:5px}}
.b{{font-size:.72rem;border-radius:12px;padding:3px 9px;background:#0d2137;
    border:1px solid #1a4a6a;color:#90caf9}}
.b-community_college{{background:#1b3a1b;border-color:#2e7d32;color:#81c784}}
.b-no_id_verification{{background:#3a1a2e;border-color:#7b1fa2;color:#ce93d8}}
.b-no_transcript_required{{background:#1a2a3a;border-color:#0277bd;color:#4fc3f7}}
.b-monthly_enrollment{{background:#2a2a1a;border-color:#f57f17;color:#ffd54f}}
.b-instant_acceptance{{background:#2a1a1a;border-color:#c62828;color:#ef9a9a}}
.b-monthly_refund{{background:#1a3a3a;border-color:#00838f;color:#80deea}}
.lnk{{display:flex;gap:12px;margin-top:2px}}
.lnk a{{color:#4fc3f7;font-size:.8rem;text-decoration:none}}
.lnk a:hover{{text-decoration:underline}}
.hidden{{display:none!important}}
footer{{text-align:center;padding:20px;color:#37474f;font-size:.78rem}}
</style>
</head>
<body>
<header>
  <div class="htop">
    <h1>CareerBridge Schools</h1>
    <span class="stat">{total} qualified schools · generated {ts}</span>
    <input id="search" type="search" placeholder="Search school name…" oninput="srch(this.value)">
  </div>
</header>
<div class="fbar">
{filter_btns}</div>
<div id="count"></div>
<div id="grid">
{cards_html}</div>
<footer>CareerBridge — online baseline · criteria: community college · no ID · no transcript · monthly enrollment · instant acceptance · monthly refund</footer>
<script>
var cards=document.querySelectorAll('.card');
var curF='', curS='';
function upd(){{
  var n=0;
  cards.forEach(function(c){{
    var fok = !curF || (' '+c.dataset.f+' ').indexOf(' '+curF+' ')>-1;
    var sok = !curS || c.dataset.n.indexOf(curS)>-1;
    var show = fok && sok;
    c.classList.toggle('hidden',!show);
    if(show) n++;
  }});
  document.getElementById('count').textContent = n+' school'+(n!==1?'s':'')+' shown';
}}
function filt(f){{
  curF=f;
  document.querySelectorAll('.fb').forEach(function(b){{
    b.classList.toggle('active', (f==='' && b.id==='btn-all') || b.id==='btn-'+f);
  }});
  upd();
}}
function srch(v){{ curS=v.toLowerCase().trim(); upd(); }}
upd();
</script>
</body>
</html>"""

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return str(out)
