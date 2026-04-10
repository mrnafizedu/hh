#!/usr/bin/env python3
"""
JS Deobfuscator — handles:
  1. Hex literals     0x0 → 0, 0x1f90 → 8080, etc.
  2. Boolean literals !![] → true, ![] → false
  3. Bracket notation obj['method'] → obj.method  (safe identifier keys)
  4. Renaming _0xABCDEF variables to readable names (sequential)
  5. Pretty-print indentation
"""

import re
import sys

# ── helpers ───────────────────────────────────────────────────────────────────
def replace_hex(m):
    return str(int(m.group(0), 16))

def deobfuscate(src: str) -> str:
    # 1. hex numbers → decimal
    src = re.sub(r'\b0x[0-9a-fA-F]+\b', replace_hex, src)

    # 2. boolean shorthands
    src = src.replace('!![]', 'true')
    src = src.replace('![]', 'false')

    # 3. bracket notation → dot notation where key is a valid JS identifier
    #    obj['foo'] → obj.foo   (skip if key has special chars or is a number)
    src = re.sub(
        r"\[\'([A-Za-z_$][A-Za-z0-9_$]*)\'\]",
        lambda m: '.' + m.group(1),
        src
    )
    # double-quoted version
    src = re.sub(
        r'\["([A-Za-z_$][A-Za-z0-9_$]*)"\]',
        lambda m: '.' + m.group(1),
        src
    )

    # 4. rename _0x<hex>  variables to readable sequential names
    seen = {}
    counter = [0]
    prefixes = {
        'const ': 'c', 'let ': 'v', 'var ': 'v', '(': 'p', ',': 'p'
    }
    def rename(m):
        name = m.group(0)
        if name not in seen:
            counter[0] += 1
            seen[name] = f'_v{counter[0]}'
        return seen[name]
    src = re.sub(r'_0x[0-9a-fA-F]{4,6}', rename, src)

    # 5. pretty-print: expand {} ; into newlines + indent
    src = _pretty(src)
    return src

def _pretty(src: str) -> str:
    """Very simple JS pretty-printer — handles { } ; and basic spacing."""
    out = []
    indent = 0
    i = 0
    in_str = False
    str_char = ''
    buf = []

    def flush():
        line = ''.join(buf).strip()
        buf.clear()
        if line:
            out.append('    ' * indent + line)

    while i < len(src):
        ch = src[i]

        # string tracking
        if not in_str and ch in ('"', "'", '`'):
            in_str = True
            str_char = ch
            buf.append(ch)
            i += 1
            continue
        if in_str:
            if ch == '\\':
                buf.append(ch)
                buf.append(src[i+1] if i+1 < len(src) else '')
                i += 2
                continue
            buf.append(ch)
            if ch == str_char:
                in_str = False
            i += 1
            continue

        if ch == '{':
            buf.append(' {')
            flush()
            indent += 1
            i += 1
            continue
        if ch == '}':
            flush()
            indent = max(0, indent - 1)
            # peek: };  or },
            nxt = src[i+1] if i+1 < len(src) else ''
            if nxt in (';', ','):
                out.append('    ' * indent + '}' + nxt)
                i += 2
            else:
                out.append('    ' * indent + '}')
                i += 1
            continue
        if ch == ';':
            buf.append(';')
            flush()
            i += 1
            continue
        # collapse multiple spaces
        if ch == ' ' and buf and buf[-1] == ' ':
            i += 1
            continue

        buf.append(ch)
        i += 1

    flush()
    return '\n'.join(out)


# ── main ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 deobfuscate.py <input.js> [output.js]")
        sys.exit(1)
    with open(sys.argv[1], 'r', encoding='utf-8') as f:
        raw = f.read()
    clean = deobfuscate(raw)
    out_path = sys.argv[2] if len(sys.argv) > 2 else sys.argv[1].replace('.js', '_clean.js')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(clean)
    print(f"Done → {out_path}")
