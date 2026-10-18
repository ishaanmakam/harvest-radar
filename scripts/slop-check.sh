#!/usr/bin/env bash
# Fails on any telltale pattern in source, styles, copy, locale files and agent prompts.
set -uo pipefail
# ripgrep must be present. On a machine without it on PATH, the Claude CLI bundles rg and runs as it when ARGV0=rg.
if ! command -v rg >/dev/null 2>&1; then
  CC_BIN="${CLAUDE_CODE_EXECPATH:-$HOME/.local/bin/claude.exe}"
  if [ -f "$CC_BIN" ]; then rg () { ARGV0=rg "$CC_BIN" "$@"; }; else echo "slop-check: rg (ripgrep) not found on PATH"; exit 1; fi
fi
DIRS=(web/src web/index.html intelligence/queue_morning.py docs/agent-voice.md)
EXISTING=(); for d in "${DIRS[@]}"; do [ -e "$d" ] && EXISTING+=("$d"); done
IGNORE=(); [ -f .slopignore ] && while read -r g; do [ -n "$g" ] && IGNORE+=(--glob "!$g"); done < .slopignore
ALLOW=$( [ -f .slopallow ] && cat .slopallow || true )
FAIL=0

check () {
  local name="$1"; shift
  local hits
  hits=$(rg -n --no-heading --glob '!**/*.md' --glob '!scripts/slop-check.sh' "${IGNORE[@]}" "$@" "${EXISTING[@]}" 2>/dev/null | while IFS= read -r line; do
    loc="${line%%:*}:$(echo "$line" | cut -d: -f2)"
    grep -qxF "$loc" <<< "$ALLOW" || echo "$line"
  done)
  if [ -n "$hits" ]; then echo "SLOP [$name]"; echo "$hits"; echo; FAIL=1; fi
}

check "dashes"            -e '—' -e '–'
check "glyphs"            -e '·' -e '→' -e '↗' -e '…' -e '\.\.\.["'"'"'`<]'
check "emoji"             -P '(?![\x{00A9}\x{00AE}\x{2122}])\p{Extended_Pictographic}'
check "exclamation"       -P '[A-Za-z0-9]!(?=[\s"'"'"'`<]|$)'
check "radius-shadow"     -e 'rounded-(sm|md|lg|xl|2xl|3xl|full)\b' -e '\bshadow-' -e 'drop-shadow' -e 'box-shadow:\s*[^n]' -e 'border-radius:\s*[1-9]'
check "glass-gradient"    -e 'backdrop-blur' -e 'backdrop-filter' -e 'bg-gradient' -e 'from-(purple|indigo|violet|pink|fuchsia|blue)' -e 'via-(purple|indigo|violet|pink|fuchsia)'
check "tailwind-palette"  -e '\b(bg|text|border|ring|from|to|via)-(slate|zinc|gray|neutral|stone|indigo|violet|purple|fuchsia|pink|emerald|teal|cyan|sky)-[0-9]{2,3}\b'
check "near-black"        -i -e '#0a0a0a' -e '#0b0b0b' -e '#111\b' -e '#111111' -e '#121212' -e '#18181b' -e '#09090b'
check "fonts"             -e 'font-mono' -e 'Inter\b' -e 'Geist' -e 'Space.Grotesk' -e 'JetBrains' -e 'Manrope' -e 'Satoshi' -e 'DM.Sans' -e 'Instrument.Serif' -e 'font-(medium|semibold|extrabold|black)\b' -e 'italic\b' -e 'tracking-(tight|tighter)'
check "ai-icons"          -e '\bSparkles?\b' -e '\bWand' -e '\bBot\b' -e 'BrainCircuit' -e '\bZap\b'
check "motion"            -e 'animate-(pulse|bounce|ping)' -e 'shimmer' -e 'marquee' -e '\blenis\b' -e 'locomotive-scroll' -e 'whileHover=.*(scale|y:)' -e 'whileInView'
check "banned-libs"       -e 'aceternity' -e 'magicui' -e 'magic-ui' -e 'react-bits' -e 'reactbits' -e 'BorderBeam' -e 'ShineBorder' -e 'ShimmerButton' -e 'MagicCard' -e 'DotPattern' -e 'RetroGrid' -e 'Particles' -e 'OrbitingCircles' -e 'Meteors' -e 'Spotlight' -e 'AuroraBackground' -e 'BackgroundBeams' -e 'BentoGrid' -e 'BlurText' -e 'ShinyText' -e 'GradientText' -e 'SplitText' -e 'TiltedCard'
check "copy-words"        -i -e '\bunlock' -e 'seamless' -e 'supercharg' -e '\bempower' -e 'revolutioni[sz]' -e 'cutting.edge' -e 'next.gen' -e 'effortless' -e 'ai.powered' -e 'game.chang' -e '\bleverag' -e '\bharness' -e '\belevate' -e 'powerful' -e 'intuitive' -e '\brobust' -e 'streamlin' -e 'actionable insight' -e 'data.driven' -e 'state.of.the.art' -e 'world.class' -e 'best.in.class' -e 'at your fingertips' -e 'peace of mind' -e 'take control' -e 'stay ahead' -e 'informed decisions' -e '\bdelve' -e 'tapestry' -e 'testament' -e 'landscape'
check "copy-frames"       -i -e '\boops\b' -e '\bwhoops\b' -e 'uh.oh' -e 'something went wrong' -e 'nothing here yet' -e 'lorem ipsum' -e 'get started' -e 'learn more' -e 'welcome back' -e "let's" -e 'great question' -e "i'd be happy" -e "here's what i found" -e 'hope this helps' -e 'it.s worth noting' -e '\bsimply\b' -e '\beffortlessly\b'
check "ai-word-in-copy"   -P '["'"'"'`>][^"'"'"'`<]*\b(AI|A\.I\.|artificial intelligence|machine learning|LLM|agentic)\b[^"'"'"'`<]*["'"'"'`<]'

if [ "$FAIL" -ne 0 ]; then echo "slop-check FAILED. Fix the cause; do not reword around the pattern."; exit 1; fi
echo "slop-check passed"
