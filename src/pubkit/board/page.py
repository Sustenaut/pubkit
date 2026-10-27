"""The board as one static HTML page: no scripts, no requests.

Each grade is a dot in SonarQube's A to E colours. Hovering it, or
tabbing to it, opens its breakdown: the start, each notch, and the
connector call each rests on. Rows come worst first.
"""

from __future__ import annotations

from html import escape

STYLE = """
:root{--bg:#f6f7f5;--surface:#fff;--fg:#1b2420;--muted:#5b6862;--line:#dde3df;--accent:#1f6b5c;
--gA:#2e9e4f;--gB:#8cc63f;--gC:#e6c229;--gD:#f08c2e;--gE:#d9473b;--ink:#1b2420;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121816;--surface:#1a2220;
--fg:#e4ebe7;--muted:#9aa8a1;--line:#2c3632;--accent:#6cc4ae;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#121816;--surface:#1a2220;--fg:#e4ebe7;--muted:#9aa8a1;--line:#2c3632;
--accent:#6cc4ae;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px 64px;display:flex;flex-direction:column;gap:16px}
h1{margin:0;font-size:28px}
p{margin:0}
.meta,.tag,.src{color:var(--muted);font-size:13px}
.problems{color:var(--gE)}
.wrap{background:var(--surface);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:middle}
th{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
tbody>tr:last-child>td{border-bottom:0}
td.mono{font-family:ui-monospace,Menlo,monospace;font-size:13px;white-space:nowrap}
.dot{display:inline-grid;place-items:center;width:24px;height:24px;border-radius:50%;
font:600 12px/1 ui-monospace,Menlo,monospace;color:#fff}
.A{background:var(--gA)}.E{background:var(--gE)}
.B{background:var(--gB);color:var(--ink)}.C{background:var(--gC);color:var(--ink)}.D{background:var(--gD);color:var(--ink)}
.overall .dot{width:30px;height:30px;font-size:14px}
.grade{position:relative;display:inline-block;border-radius:50%;cursor:default}
.grade:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.pop{display:none;position:absolute;z-index:2;top:calc(100% + 6px);right:-8px;width:min(460px,86vw);
background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:10px 12px;
box-shadow:0 6px 24px rgb(0 0 0/.18)}
.grade:hover .pop,.grade:focus .pop,.grade:focus-within .pop{display:block}
.pop table{width:100%}
.pop td{padding:4px 6px;border-bottom:1px solid var(--line);font-size:13px;white-space:normal;
vertical-align:top}
.pop tr:last-child td{border-bottom:0;font-weight:600}
.pop td.n{white-space:nowrap;font-family:ui-monospace,Menlo,monospace}
@media (max-width:760px){
.wrap>table>thead{display:none}
.wrap>table>tbody>tr{display:grid;grid-template-columns:repeat(3,1fr);gap:6px 12px;padding:12px;
border-bottom:1px solid var(--line)}
.wrap>table>tbody>tr>td{border:0;padding:0}
.wrap>table>tbody>tr>td:first-child{grid-column:1/-1;white-space:normal}
.wrap>table>tbody>tr>td[data-label]::before{content:attr(data-label);display:block;font-size:11px;
color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.pop{position:fixed;top:auto;left:16px;right:16px;bottom:16px;width:auto;max-height:70vh;overflow-y:auto}
}
"""


def source_label(step: dict, calls: dict[str, dict]) -> str:
    """The connectors and methods a step rests on: "github · issues, rulesets"."""
    methods: dict[str, list[str]] = {}
    for source in step.get("sources", []):
        call = calls.get(source)
        connector, method = (call["connector"], call["method"]) if call else ("call", source)
        if method not in methods.setdefault(connector, []):
            methods[connector].append(method)
    return "; ".join(f"{c} · {', '.join(m)}" for c, m in methods.items()) or \
        ", ".join(f"fact {f}" for f in step.get("facts", []))


def breakdown(name: str, grade: dict, calls: dict[str, dict], ident: str) -> str:
    start = grade["start"]
    rows = [f"<tr><td>{escape(start['text'])}</td><td class=n><span class=\"dot {start['letter']}\">"
            f"{start['letter']}</span></td><td class=src>{escape(source_label(start, calls))}</td></tr>"]
    for step in grade["steps"]:
        delta = "−1" if step["delta"] < 0 else "note"
        rows.append(f"<tr><td>{escape(step['text'])}</td><td class=n>{delta}</td>"
                    f"<td class=src>{escape(source_label(step, calls))}</td></tr>")
    rows.append(f"<tr><td>{escape(name)}</td><td class=n><span class=\"dot {grade['letter']}\">"
                f"{grade['letter']}</span></td><td class=src>{grade['notches']} notch"
                f"{'es' if grade['notches'] != 1 else ''}</td></tr>")
    return (f"<span class=grade tabindex=0 aria-describedby={ident}>"
            f"<span class=\"dot {grade['letter']}\" aria-label=\"{escape(name)} {grade['letter']}\">"
            f"{grade['letter']}</span><span class=pop role=tooltip id={ident}><table><tbody>"
            f"{''.join(rows)}</tbody></table></span></span>")


def render(board: dict) -> str:
    calls = {c["id"]: c for c in board["calls"]}
    rows = []
    for n, row in enumerate(board["repos"]):
        private = " <span class=tag>private</span>" if row["visibility"] == "private" else ""
        cells = "".join(
            f"<td data-label={label}>{breakdown(label, row['grades'][key], calls, f'r{n}-{key[0]}')}</td>"
            for key, label in (("quality", "Quality"), ("security", "Security"),
                               ("delivery", "Delivery")))
        overall = row["overall"]
        rows.append(
            f"<tr><td class=mono>{escape(row['repo'])}{private} <span class=tag>{row['kind']}</span></td>"
            f"<td class=overall data-label=Overall><span class=\"dot {overall}\">{overall}</span></td>"
            f"<td class=mono data-label=Released>{escape(row['released'])}</td>"
            f"<td class=mono data-label=pubkit>{escape(row['pubkit'])}</td>"
            f"{cells}</tr>")
    problems = "".join(f"<p class=problems>{escape(p)}</p>" for p in board.get("problems", []))
    return f"""<!doctype html>
<html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Release board</title><style>{STYLE}</style></head>
<body><main>
<h1>Release board</h1>
<p>{escape(board['summary'])}</p>
<p class=meta>Graded by pubkit {escape(board['pubkit'])} with the rules "{escape(board['rules'])}".
Hover or tab to a grade for how it was reached; board.json has every reading.</p>
{problems}
<div class=wrap><table>
<thead><tr><th>Repository</th><th>Overall</th><th>Released</th><th>pubkit</th><th>Quality</th>
<th>Security</th><th>Delivery</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody></table></div>
</main></body></html>
"""
