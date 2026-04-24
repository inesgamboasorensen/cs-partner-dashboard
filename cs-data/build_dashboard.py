#!/usr/bin/env python3
"""Build cs-dashboard.html by embedding dashboard_data_embed.json into template."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
TPL = os.path.join(HERE, 'dashboard_template.html')
DATA = os.path.join(HERE, 'dashboard_data_embed.json')
OUT = os.path.join(PROJ, 'cs-dashboard.html')

with open(TPL) as f: tpl = f.read()
with open(DATA) as f: data = f.read().strip()
# Safe-guard against </script> appearing in data
data = data.replace('</script>', '<\\/script>')
html = tpl.replace('/*__DATA__*/', data)
with open(OUT, 'w') as f: f.write(html)
print(f'Wrote {OUT} ({len(html)//1024} KB)')
