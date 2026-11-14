"""Structural and color checks for optional dashboard previews, without a browser."""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess


_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')


def _parse_color(color):
    if not isinstance(color, str) or not _COLOR_RE.fullmatch(color):
        raise ValueError('color must be a six-digit #RRGGBB hex string')
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def luminance(color):
    def linearize(channel):
        channel /= 255.0
        return channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4
    red, green, blue = map(linearize, _parse_color(color))
    return .2126 * red + .7152 * green + .0722 * blue


def contrast(first, second):
    one, two = luminance(first), luminance(second)
    return (max(one, two) + .05) / (min(one, two) + .05)


PAIRS = [('text', 'bg', 4.5), ('text', 'panel', 4.5), ('muted', 'bg', 4.5),
         ('muted', 'panel', 4.5), ('accent', 'bg', 4.5), ('accent', 'panel', 4.5),
         ('onAccent', 'accent', 4.5), ('good', 'panel', 4.5), ('danger', 'panel', 4.5),
         ('line', 'bg', 3.), ('line', 'panel', 3.)]


def audit_palette(palette):
    roles = {'bg', 'panel', 'text', 'muted', 'accent', 'onAccent', 'line', 'good', 'danger'}
    if not isinstance(palette, dict) or set(palette) != roles:
        raise ValueError('palette must contain exactly the required roles')
    for color in palette.values():
        _parse_color(color)
    return [first + '/' + second for first, second, threshold in PAIRS
            if contrast(palette[first], palette[second]) < threshold]


def hidden_options():
    if os.name != 'nt':
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {'creationflags': subprocess.CREATE_NO_WINDOW, 'startupinfo': startup}


def manifest(project, include_combined=False):
    script = project / 'src/gamma/dashboard/static/coordination_designs.js'
    program = ('const fs=require("node:fs"),vm=require("node:vm"); const c={window:{}}; '
               'vm.runInNewContext(fs.readFileSync(process.argv[1],"utf8"),c); '
               'process.stdout.write(JSON.stringify(' +
               ('c.window.ShanaDesigns.previewStyles' if include_combined else 'c.window.ShanaDesigns.styles') + '));')
    result = subprocess.run(['node', '-e', program, str(script)], capture_output=True, text=True,
                            timeout=20, check=True, **hidden_options())
    return json.loads(result.stdout)


def balanced_css(source):
    """Check delimiters while respecting comments and quoted strings, not full CSS grammar."""
    stack, quote, comment, escaped = [], None, False, False
    index = 0
    pairs = {')': '(', ']': '[', '}': '{'}
    while index < len(source):
        char = source[index]
        if comment:
            if source[index:index + 2] == '*/':
                comment = False; index += 2; continue
        elif quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif source[index:index + 2] == '/*':
            comment = True; index += 2; continue
        elif char in '\"\'':
            quote = char
        elif char in '([{':
            stack.append(char)
        elif char in ')]}':
            if not stack or stack.pop() != pairs[char]:
                raise ValueError('unbalanced CSS delimiter at character ' + str(index))
        index += 1
    if stack or quote or comment:
        raise ValueError('unterminated CSS delimiter, quote or comment')
    return True


def verify(project):
    styles = manifest(project)
    if len(styles) != 10 or len({s['id'] for s in styles}) != 10:
        raise ValueError('exactly ten distinct preview IDs are required')
    if len({s['layout'] for s in styles}) != 10:
        raise ValueError('directions need distinct composition/navigation descriptions')
    previews = manifest(project, include_combined=True)
    if len(previews) != 11 or previews[:10] != styles or previews[-1]['id'] != 'night-signal':
        raise ValueError('the combined preview must preserve all ten original directions')
    styles = previews
    css = (project / 'src/gamma/dashboard/static/coordination_designs.css').read_text(encoding='utf-8')
    balanced_css(css)
    report = []
    for style in styles:
        if not re.fullmatch(r'[a-z][a-z0-9-]{2,40}', style['id']):
            raise ValueError('invalid preview identifier')
        if 'body[data-shana-style=' + style['id'] + ']' not in css or '[data-design=' + style['id'] + ']' not in css:
            raise ValueError('missing working-page or static-sample style rules')
        failures = audit_palette(style['colors'])
        if failures:
            raise ValueError(style['id'] + ' contrast: ' + ', '.join(failures))
        report.append({'id': style['id'], 'name': style['name'], 'layout': style['layout'],
                       'density': style['density'], 'failing_roles': failures,
                       'ratios': {a + '/' + b: round(contrast(style['colors'][a], style['colors'][b]), 3)
                                  for a, b, _ in PAIRS}})
    for required in ('[hidden]{display:none!important}', '@media(max-width:760px)', ':focus-visible', 'min-height:44px', 'prefers-reduced-motion'):
        if required not in css:
            raise ValueError('missing structural/privacy/focus rule: ' + required)
    return report


def standalone(project, target):
    if target.exists():
        raise ValueError('standalone export must not replace an existing file')
    html = (project / 'src/gamma/coordination/designs.html').read_text(encoding='utf-8')
    css = (project / 'src/gamma/dashboard/static/coordination_designs.css').read_text(encoding='utf-8')
    script = (project / 'src/gamma/dashboard/static/coordination_designs.js').read_text(encoding='utf-8')
    if '</script' in script.lower() or '</style' in css.lower():
        raise ValueError('inline export contains an unsafe closing tag')
    replacements = {
        '<link rel="stylesheet" href="/static/coordination_shell.css">': '',
        '<script src="/static/coordination_shell.js" defer></script>': '',
        '<link rel="stylesheet" href="/static/coordination_designs.css">': '<style>' + css + '</style>',
        '<script src="/static/coordination_designs.js" defer></script>': '<script>' + script + '</script>',
        '<meta name="author" content="darke@Gaming">': '<meta name="author" content="' + getpass.getuser() + '@' + socket.gethostname() + '">',
    }
    for old, new in replacements.items():
        if html.count(old) != 1:
            raise ValueError('standalone source contract changed: ' + old)
        html = html.replace(old, new)
    html = html.replace('<body class="design-gallery">', '<body class="design-gallery"><p class="design-gallery-note" style="padding:12px 24px">Saved static gallery. Fictional content only; no server or private history is loaded.</p>')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding='utf-8')
    return hashlib.sha256(target.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--report', type=Path)
    parser.add_argument('--standalone', type=Path)
    args = parser.parse_args()
    styles = verify(args.project)
    report = {'status': 'structural_and_palette_checks_passed', 'styles': styles,
              'author': getpass.getuser() + '@' + socket.gethostname(),
              'created_utc': datetime.now(timezone.utc).isoformat(),
              'rendered': False, 'brave_visual_verification': False, 'physical_phone': False,
              'default_changed': True, 'default_scope': 'isolated_dashboard_only',
              'isolated_dashboard_default': 'night-signal', 'production_default_changed': False,
              'visual_acceptance': 'combined_direction_accepted_actual_use_pending',
              'limits': ['Palette roles and CSS delimiters are checked; computed layout, font rendering and actual usability remain unverified.',
                         'No browser automation or screenshot capture was used.'],
              'source_hashes': {name: hashlib.sha256((args.project / name).read_bytes()).hexdigest() for name in
                  ['src/gamma/coordination/designs.html', 'src/gamma/dashboard/static/coordination_designs.js',
                   'src/gamma/dashboard/static/coordination_designs.css', 'scripts/verify_dashboard_designs.py']}}
    if args.standalone:
        report['standalone'] = {'path': str(args.standalone), 'sha256': standalone(args.project, args.standalone)}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'styles': len(styles), 'rendered': False,
                      'report': str(args.report) if args.report else None, 'standalone': report.get('standalone')}))


if __name__ == '__main__':
    main()
