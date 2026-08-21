COMPILE = ttx -f --recalc-timestamp
CPP = cpp
PYTHON ?= python3
HANGUL_FONT ?= font-mix/DungGeunMo.ttf
MERGE_HANGUL = $(PYTHON) scripts/merge_hangul.py
VERIFY_FONTS = $(PYTHON) scripts/verify_fonts.py

.PHONY: all verify
.INTERMEDIATE: FSEX-default.ttx FSEX-alt.ttx FSEX-default-base.ttf FSEX-alt-base.ttf

all: FixedsysEX.ttf FixedsysEX-alt.ttf
	cp FixedsysEX.ttf ~/Library/Fonts
	atsutil databases -remove

verify: FixedsysEX.ttf FixedsysEX-alt.ttf
	$(VERIFY_FONTS) $(HANGUL_FONT) FixedsysEX.ttf FixedsysEX-alt.ttf

FixedsysEX.ttf: FSEX-default-base.ttf $(HANGUL_FONT) scripts/merge_hangul.py
	$(MERGE_HANGUL) $< $(HANGUL_FONT) $@

FixedsysEX-alt.ttf: FSEX-alt-base.ttf $(HANGUL_FONT) scripts/merge_hangul.py
	$(MERGE_HANGUL) $< $(HANGUL_FONT) $@

FSEX-default-base.ttf: FSEX-default.ttx
	$(COMPILE) -o $@ $<

FSEX-alt-base.ttf: FSEX-alt.ttx
	$(COMPILE) -o $@ $<

FSEX-default.ttx: FSEX.ttx
	$(CPP) $< |grep -v "^#" > $@ 

FSEX-alt.ttx: FSEX.ttx
	$(CPP) -DALT_LESSEQUAL $< |grep -v "^#" > $@
