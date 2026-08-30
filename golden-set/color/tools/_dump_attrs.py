"""v3 18색 attributes dump (one-off)."""
import json
with open('golden-set/color/rules/color_rules.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
attrs = data['attributes']
print(f'count: {len(attrs)}')
for name in sorted(attrs.keys()):
    a = attrs[name]
    hex_v = a['hex']
    role = a['role']
    temp = a['temperature']
    chroma = a['chroma']
    effect = a['visual_effect']
    print(f'  {name:14s} hex={hex_v:8s} role={role:8s} temp={temp:8s} chroma={chroma:5.1f} effect={effect}')
