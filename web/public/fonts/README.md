# D-DIN webfonts

Family: D-DIN, by Datto Inc. Design: Charles Nix (Monotype). First public release 2017-10-26 as v1.0; the font name table reports "Version 1.00".

Licence: SIL Open Font License 1.1, with Reserved Font Name "D-DIN". The full licence text as shipped with the font is in OFL-D-DIN.txt in this directory.

## Files

| File | Face | Weight | Size | SHA-256 |
|---|---|---|---|---|
| D-DIN.woff2 | D-DIN Regular | 400 | 20744 bytes | 41b210fad6e9eaa120e032bb945a2e4cf30dece4de3657ec0dc42732ba472740 |
| D-DIN-Bold.woff2 | D-DIN Bold | 700 | 22052 bytes | 9007288706aafe9616f96be4e856fe91722056c40ea579bbfcd95993a6e82e34 |
| OFL-D-DIN.txt | licence text (OFL-1.1.txt from the release) | | 4624 bytes | 59e505e11e6da3ac3020eb6b4163f4d0382524183db660bf52a08234c764b2e5 |

The woff2 files are the webfont builds shipped in the Datto release, copied as is with no conversion. Decompressed, their tables and glyph outlines match the TTF files from the same release.

## Source

- Fetched 2026-10-18 from https://github.com/amcchord/datto-d-din (branch main, commit e199c8441e758d6e492cd01ef52c3c67ba4bae26), which redistributes the complete Datto release unchanged: otf, ttf, woff and woff2 builds, FONTLOG.txt, OFL-1.1.txt and AppStream metainfo.
- The original Datto page https://www.datto.com/fonts/d-din returned HTTP 404 on 2026-10-18. The same release is packaged by Fedora as d-din-fonts.
- Only the Regular and Bold faces of the normal width are kept here. Italic, Condensed and Expanded faces are in the source repository.

## Font facts read with fontTools

- family name (name id 1): D-DIN for both files
- subfamily (name id 2): Regular, DIN-Bold; full names: D-DIN, D-DIN Bold; PostScript: D-DIN, D-DIN-Bold
- OS/2 usWeightClass: 400, 700; usWidthClass: 5
- 251 glyphs, 231 cmap entries: Basic Latin and Latin-1 Supplement complete, plus the ISO 8859-15 extras
- unitsPerEm 1000, TrueType outlines, 18 tables per file

The @font-face rules that load these files live in web/src/tokens.css.
