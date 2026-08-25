COMPILE = ttx -f --recalc-timestamp
CPP = cpp
PYTHON ?= python3
HANGUL_FONT ?= font-mix/DungGeunMo.ttf
MERGE_HANGUL = $(PYTHON) scripts/merge_hangul.py
VERIFY_FONTS = $(PYTHON) scripts/verify_fonts.py
WOFF2_COMPRESS = $(PYTHON) -m fontTools.ttLib.woff2 compress
TTF_FONTS = FixedsysEX.ttf FixedsysEX-alt.ttf
WOFF2_FONTS = FixedsysEX.woff2 FixedsysEX-alt.woff2
FONT_OUTPUTS = $(TTF_FONTS) $(WOFF2_FONTS)

.PHONY: all verify
.INTERMEDIATE: FSEX-default.ttx FSEX-alt.ttx FSEX-default-base.ttf FSEX-alt-base.ttf

all: $(FONT_OUTPUTS)
	cp FixedsysEX.ttf ~/Library/Fonts
	atsutil databases -remove

verify: $(FONT_OUTPUTS)
	$(VERIFY_FONTS) $(HANGUL_FONT) $(FONT_OUTPUTS)

FixedsysEX.ttf: FSEX-default-base.ttf $(HANGUL_FONT) scripts/merge_hangul.py
	$(MERGE_HANGUL) $< $(HANGUL_FONT) $@

FixedsysEX-alt.ttf: FSEX-alt-base.ttf $(HANGUL_FONT) scripts/merge_hangul.py
	$(MERGE_HANGUL) $< $(HANGUL_FONT) $@

FixedsysEX.woff2: FixedsysEX.ttf
	$(WOFF2_COMPRESS) $< -o $@

FixedsysEX-alt.woff2: FixedsysEX-alt.ttf
	$(WOFF2_COMPRESS) $< -o $@

FSEX-default-base.ttf: FSEX-default.ttx
	$(COMPILE) -o $@ $<

FSEX-alt-base.ttf: FSEX-alt.ttx
	$(COMPILE) -o $@ $<

FSEX-default.ttx: FSEX.ttx
	$(CPP) $< |grep -v "^#" > $@ 

FSEX-alt.ttx: FSEX.ttx
	$(CPP) -DALT_LESSEQUAL $< |grep -v "^#" > $@
