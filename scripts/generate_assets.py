"""Original SVG artwork; regenerate without external tools or downloaded assets."""
import json
import math
from pathlib import Path

out = Path(__file__).resolve().parents[1] / "apps/web/public/assets"
out.mkdir(parents=True, exist_ok=True)
width, height = 320, 450
frames, animations, drawings = {}, {}, []


def character(emotion, action, i, count):
    phase = i / count * math.tau
    bob = round(math.sin(phase) * 2, 2)
    lean = -5 * math.sin(i / max(count - 1, 1) * math.pi) if action == "look_window" else 0
    eyes = '<path d="M128 138q10 -5 19 0m24 0q10 -5 19 0"/>' if emotion == "warm" else '<path d="M129 139h17m27 0h17"/><circle cx="139" cy="140" r="3"/><circle cx="182" cy="140" r="3"/>'
    eyes += '<path d="M128 125l19 5m26 0l19 -5" fill="none"/>' if emotion == 'guarded' else '<path d="M128 122q10 -7 19 0m26 5h19" fill="none"/>' if emotion == 'curious' else ''
    if action == 'thinking':
        eyes = '<path d="M129 138h17m27 0h17"/><circle cx="142" cy="136" r="3"/><circle cx="184" cy="136" r="3"/><path d="M128 125h19m26 0h19"/>'
    if action == 'listening':
        lean = 4
    mouth = '<path d="M151 169q12 8 23 -1" fill="none"/>' if emotion == "warm" else '<path d="M153 172q10 -3 20 0" fill="none"/>'
    if action == "speaking":
        mouth = f'<ellipse cx="164" cy="171" rx="{5+i%3}" ry="{3+i%2*3}" fill="#875147"/>'
    if action == "idle" and i == 1:
        eyes = '<path d="M129 140h17m27 0h17"/>'
    photo = '<g transform="translate(106 264) rotate(-7)"><rect width="107" height="81" rx="3" fill="#fff5db"/><rect x="8" y="8" width="91" height="52" fill="#719eac"/><path d="M8 60l40 -28 27 17 24 -17v28" fill="#e1bf76"/></g>' if action == "hold_photo" else ''
    arms = '<path d="M82 242Q54 315 116 320m123 -76q31 63 -39 76" stroke="#b77935" stroke-width="32" fill="none"/>' if photo else '<path d="M82 242q-16 61 -13 120m172 -119q18 59 11 118" stroke="#bb823c" stroke-width="34" fill="none"/>'
    return f'''<g transform="translate({lean} {bob})" stroke="#352d2c" stroke-width="2" stroke-linecap="round">
    <ellipse cx="161" cy="426" rx="91" ry="12" fill="#132128" opacity=".2" stroke="none"/>
    <path d="M109 345l-4 74h40l16 -69 16 69h40l-8 -74" fill="#283841"/>
    <path d="M88 211q73 -33 147 0l11 161q-83 26 -169 0z" fill="#cb9447"/>
    {arms}<path d="M112 207l45 39 -24 48 -31 -66m110 -21l-45 39 24 48 31 -66" fill="#e1b767"/>
    <path d="M160 247v135" stroke="#8d662e"/><circle cx="172" cy="307" r="3" fill="#56482c"/>
    <path d="M139 179v39q21 23 43 0v-40" fill="#dfb49a"/>
    <path d="M93 138q-11 -70 58 -78 67 -8 78 65l-4 97 -26 -17 -94 15z" fill="#343638"/>
    <path d="M111 110q0 -35 53 -31 49 4 47 42l-9 51q-40 48 -80 2z" fill="#e8c2a5"/>
    <path d="M106 117q8 -64 66 -48 39 -2 47 57 -34 -7 -56 -36 -19 22 -57 27" fill="#343638"/>
    <g stroke="#57443c" stroke-width="2">{eyes}{mouth}<path d="M160 147l-4 10h6" fill="none" opacity=".6"/></g>
    <path d="M209 91l3 8 8 1 -6 6 1 8 -7 -4 -7 4 2 -8 -6 -6 9 -1z" fill="#d8e2e4" stroke="#81969d"/>
    {photo}</g>'''


index = 0
for emotion in ("guarded", "curious", "warm"):
    for action, count in (("idle", 2), ("listening", 2), ("thinking", 2), ("speaking", 4), ("look_window", 6), ("hold_photo", 6)):
        names = []
        for i in range(count):
            name = f"{emotion}-{action}-{i}"
            x, y = index % 8 * width, index // 8 * height
            frames[name] = {"frame": {"x": x, "y": y, "w": width, "h": height}, "rotated": False, "trimmed": False,
                "spriteSourceSize": {"x": 0, "y": 0, "w": width, "h": height}, "sourceSize": {"w": width, "h": height}}
            drawings.append(f'<g transform="translate({x} {y})">{character(emotion, action, i, count)}</g>')
            names.append(name)
            index += 1
        animations[f"{emotion}-{action}"] = names
(out / "mira.svg").write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="2560" height="4050">{"".join(drawings)}</svg>', encoding="utf-8")
(out / "mira.json").write_text(json.dumps({"frames": frames, "animations": animations, "meta": {"image": "mira.svg", "scale": "1", "size": {"w": 2560, "h": 4050}}}), encoding="utf-8")
(out / "cafe.svg").write_text('''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 900">
<defs><linearGradient id="wall" x2="0" y2="1"><stop stop-color="#142b33"/><stop offset="1" stop-color="#30444a"/></linearGradient><radialGradient id="light"><stop stop-color="#edb65f" stop-opacity=".48"/><stop offset="1" stop-color="#edb65f" stop-opacity="0"/></radialGradient><linearGradient id="glass" x2="0" y2="1"><stop stop-color="#153845"/><stop offset="1" stop-color="#396473"/></linearGradient></defs>
<path fill="url(#wall)" d="M0 0h1000v900H0z"/><circle cx="773" cy="259" r="270" fill="url(#light)"/>
<rect x="48" y="88" width="544" height="559" rx="8" fill="#0a1c24"/><rect x="65" y="103" width="508" height="520" fill="url(#glass)"/>
<path d="M73 524h494v99H73z" fill="#263b46"/><path d="M91 503v-90h61v90m45 0V361h87v142m56 0V404h54v99m54 0V344h95v159" fill="#142a35"/>
<g fill="#dcb676" opacity=".65"><path d="M217 382h9v13h-9zm29 0h9v13h-9zm-29 32h9v13h-9zm268 -49h8v12h-8zm25 0h8v12h-8z"/></g>
<g stroke="#9ab8c5" stroke-width="2" opacity=".4"><path d="M100 150l-10 70m80 -49l-15 112m-30 96l-12 78m146 -265l-20 132m74 -70l-13 97m64 -193l-22 143m68 8l-17 96m69 -208l-21 131m-209 121l-18 116m-67 -62l-16 78m345 -139l-15 132"/></g>
<path d="M315 101v526M63 357h511" stroke="#15252b" stroke-width="15"/>
<path d="M749 0v182" stroke="#71604b" stroke-width="5"/><path d="M698 181h102l36 54H662z" fill="#d1a76d"/><ellipse cx="750" cy="235" rx="85" ry="9" fill="#f6d89a"/>
<rect x="664" y="335" width="253" height="175" fill="#21343a" stroke="#8a6c47" stroke-width="8"/><text x="790" y="415" text-anchor="middle" fill="#ceb58c" font-family="Georgia,serif" font-size="30">AFTER THE RAIN</text><text x="790" y="457" text-anchor="middle" fill="#7f989d" font-family="Georgia,serif" font-size="16">coffee &amp; little stories</text>
<path d="M0 712q500 -68 1000 0v188H0z" fill="#40372d"/><path d="M0 724q500 -64 1000 0" stroke="#977650" stroke-width="5"/>
<ellipse cx="200" cy="747" rx="64" ry="18" fill="#1f2c31" opacity=".5"/><path d="M156 696h68l-9 48h-49z" fill="#e7dcc4"/><path d="M224 706q34 0 17 27h-22" fill="none" stroke="#e7dcc4" stroke-width="9"/><ellipse cx="190" cy="696" rx="34" ry="8" fill="#685345"/>
<path d="M829 707v-44m-1 32q-66 -13 -37 -56 34 6 38 33m1 20q64 -30 35 -57 -39 5 -35 40" stroke="#426d57" stroke-width="9" fill="#41684f"/><path d="M790 701h81l-12 52h-58z" fill="#956c4f"/>
</svg>''', encoding="utf-8")
(out / "photo.svg").write_text('''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 300"><defs><linearGradient id="sea" x2="0" y2="1"><stop stop-color="#7fb4b6"/><stop offset="1" stop-color="#376f84"/></linearGradient></defs><path fill="#fff3d8" d="M0 0h420v300H0z"/><path fill="#ecd3a0" d="M18 18h384v231H18z"/><path fill="url(#sea)" d="M18 103h384v146H18z"/><circle cx="279" cy="82" r="32" fill="#fff0b4"/><path d="M270 114l-73 135h167l-77 -135" fill="#e6cc80" opacity=".65"/><path d="M18 220q143 -40 384 4v25H18z" fill="#d1b17f"/><g fill="#354750"><circle cx="111" cy="162" r="13"/><path d="M99 177h25l10 45h-45z"/><circle cx="145" cy="159" r="13"/><path d="M134 173h24l10 49h-43z"/></g><text x="210" y="282" text-anchor="middle" fill="#826746" font-family="Georgia,serif" font-size="15">a day we kept · 2019</text></svg>''', encoding="utf-8")
print(f"Created {index} original animation frames.")
