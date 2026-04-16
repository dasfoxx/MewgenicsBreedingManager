# Mewgenics Breeding Manager

My fork for figuring out visual data within Mewgenics
See main https://github.com/frankieg33/MewgenicsBreedingManager 
Reads images from GPAK using a GPAK extractor then proceeds to use the ffdec_lib from JPEXS 
to export them out as PNG btye data. Bytes are saved to increase load speed and remain TOS compliant
Mapped cat texture, palette, base body parts
Databases created from .xmls of .swf are used to find correct PNG data and bounds to create thumbnail of cat
Several databases require specific individual patterns to work
TODO: ancestry depth buttons
TODO: Jester and Collarless, Portraits matching ingame (can't quite get the positioning to match)
TODO: clear PNG bytes so it can be updated with game updates
TODO: add xml export of used swf so databases can also be cleared and updated with game updates
TODO: portrait positioning
TODO: portrait thumbnail
Will come back to later if/when figured out. Check notes to remember spot

## Install

This project uses `pip` and `requirements.txt`.

```bash
git clone https://github.com/frankieg33/MewgenicsBreedingManager
cd MewgenicsBreedingManager
pip install -r requirements.txt
python src/mewgenics_manager.py
```

The app will automatically look for `resources.gpak` in common Steam install paths, the app's configured save root, or in the current working directory.

## Build

```bash
build.bat
```

On Linux, use `build.sh`.

## Requirements

- Python 3.14
- PySide6
- lz4
- openpyxl

## Attribution & Licenses

This project uses third-party libraries with their respective licenses:

- **ffdec_lib** ([JPEXS Free Flash Decompiler](https://github.com/jindrapetrik/jpexs-decompiler))
  - Used under LGPL license to parse image data from game GPAK archives
  - Source code available at: https://github.com/jindrapetrik/jpexs-decompiler/releases
  - A copy of the LGPL license is included in [LICENSE-LGPL.txt](./src/CatAssets/ffdec_lib_26.0.0/license.txt)

## Credits

- Save parsing research based on [pzx521521/mewgenics-save-editor](https://github.com/pzx521521/mewgenics-save-editor)
- Community reverse-engineering help from players and mod users
- PR contributors: [0demongamer0](https://github.com/0demongamer0), [An-on-im](https://github.com/An-on-im), [byronaltice](https://github.com/byronaltice), [heartskingu](https://github.com/heartskingu), [ICaxapl](https://github.com/ICaxapl), [luisMolina95](https://github.com/luisMolina95), [TheMegax](https://github.com/TheMegax), [dasfoxx](https://github.com/dasfoxx)
- Simulated annealing (SA) idea from [PurpleMyst](https://github.com/PurpleMyst/mewgenics_breeding_helper)
- Original idea and reference from frankieg33